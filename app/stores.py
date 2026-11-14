"""Store factories with explicit availability reporting (no silent fallbacks in production)."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

from app.repository import MetadataRepository
from app.repository_mongo import MongoMetadataRepository
from app.request_log_repository import MongoRequestLogRepository, RequestLogRepository
from app.schemas import DocumentMetadata

logger = logging.getLogger("instaretriv.stores")

_MAX_ERROR_LENGTH = 300
_URI_CREDENTIALS = re.compile(r"(//)[^/@\s]+@")


class StoreUnavailableError(RuntimeError):
    """The metadata store cannot be reached right now (callers should answer 503 / a friendly retry message)."""


def _unavailable_error_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = [ConnectionError, TimeoutError, OSError]
    try:
        from pymongo.errors import ConfigurationError, ConnectionFailure, ExecutionTimeout

        types.extend([ConnectionFailure, ConfigurationError, ExecutionTimeout])
    except ImportError:  # pragma: no cover - pymongo is a hard dependency in production
        pass
    return tuple(types)


_UNAVAILABLE_ERRORS = _unavailable_error_types()
# Mongo auth failures surface as OperationFailure with these codes; the store is unusable until fixed.
_AUTH_FAILURE_CODES = {13, 18}


def is_unavailable_error(exc: BaseException) -> bool:
    if isinstance(exc, (StoreUnavailableError, *_UNAVAILABLE_ERRORS)):
        return True
    return type(exc).__name__ == "OperationFailure" and getattr(exc, "code", None) in _AUTH_FAILURE_CODES


def describe_error(exc: BaseException) -> str:
    """Short, credential-free description of an error for status pages and logs."""
    message = str(exc).strip() or type(exc).__name__
    message = _URI_CREDENTIALS.sub(r"\1***@", message)
    if len(message) > _MAX_ERROR_LENGTH:
        message = message[: _MAX_ERROR_LENGTH - 1] + "…"
    return message


class ResilientMetadataStore:
    """Wraps a metadata repository built lazily by ``factory`` and reports its availability.

    - Building the repository failing (any exception) -> error recorded, StoreUnavailableError raised;
      the next build attempt happens at most every ``retry_seconds``.
    - A call failing with a connection-type error -> error recorded, StoreUnavailableError raised;
      the repository is kept (its client reconnects on its own) and the next successful call clears the error.
      For ``retry_seconds`` after such a failure calls fail fast (circuit open, no backend wait); after that a
      single probe call is let through and its outcome closes or re-opens the circuit.
    - Any other exception from a call propagates unchanged.
    - ``status()`` probes the backend with ``ping`` at most every ``probe_interval_seconds`` so health checks
      reflect outages and recoveries even when no other traffic happens.
    """

    def __init__(
        self,
        factory: Callable[[], Any],
        backend_name: str,
        retry_seconds: int = 30,
        *,
        fallback: bool = False,
        fallback_reason: str | None = None,
        clock: Callable[[], float] | None = None,
        probe_interval_seconds: float = 5.0,
    ) -> None:
        self._factory = factory
        self.backend_name = backend_name
        self.retry_seconds = max(0, int(retry_seconds))
        self.fallback = fallback
        self.fallback_reason = fallback_reason
        self._clock = clock
        self.probe_interval_seconds = max(0.0, float(probe_interval_seconds))
        self._lock = threading.Lock()
        self._repository: Any | None = None
        self._last_attempt: float | None = None
        self._error: str | None = None
        # When the last call failed (circuit open until failed_at + retry_seconds).
        self._failed_at: float | None = None
        self._last_probe: float | None = None

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    def _record_error(self, exc: BaseException, action: str) -> None:
        self._error = describe_error(exc)
        self._failed_at = self._now()
        logger.error("Metadata store (%s) unavailable during %s: %s", self.backend_name, action, self._error)

    def _get_repository(self) -> Any:
        repository = self._repository
        if repository is not None:
            return repository
        with self._lock:
            if self._repository is not None:
                return self._repository
            now = self._now()
            if self._last_attempt is not None and now - self._last_attempt < self.retry_seconds:
                raise StoreUnavailableError(self._error or f"{self.backend_name} metadata store unavailable")
            self._last_attempt = now
            try:
                self._repository = self._factory()
            except Exception as exc:
                self._record_error(exc, "connect")
                raise StoreUnavailableError(self._error) from exc
            if self._error is not None:
                logger.info("Metadata store (%s) connected", self.backend_name)
            self._error = None
            self._failed_at = None
            self._last_probe = now
            return self._repository

    def _check_circuit(self) -> None:
        """Fail fast while a recent call failure is within ``retry_seconds``; then let one probe through."""
        if self._failed_at is None:
            return
        with self._lock:
            failed_at = self._failed_at
            if failed_at is None:
                return
            now = self._now()
            if now - failed_at < self.retry_seconds:
                raise StoreUnavailableError(self._error or f"{self.backend_name} metadata store unavailable")
            # Half-open: this call is the probe; concurrent callers keep failing fast until it finishes.
            self._failed_at = now

    def _call(self, method: str, *args: Any) -> Any:
        repository = self._get_repository()
        self._check_circuit()
        try:
            result = getattr(repository, method)(*args)
        except StoreUnavailableError as exc:
            self._error = str(exc) or self._error
            self._failed_at = self._now()
            raise
        except Exception as exc:
            if not is_unavailable_error(exc):
                raise
            self._record_error(exc, method)
            raise StoreUnavailableError(self._error) from exc
        if self._error is not None:
            logger.info("Metadata store (%s) recovered", self.backend_name)
            self._error = None
        self._failed_at = None
        return result

    def connect(self) -> bool:
        """Try to build the repository now (respecting the retry interval). Returns availability."""
        try:
            self._get_repository()
        except StoreUnavailableError:
            return False
        return True

    @property
    def last_error(self) -> str | None:
        return self._error

    @property
    def available(self) -> bool:
        return self._repository is not None and self._error is None

    def status(self) -> dict[str, Any]:
        """{"backend", "available", "error", "fallback"}. May attempt a (rate-limited) connection; never raises."""
        if self.connect():
            self._probe()
        available = self.available
        error = self._error
        if available and self.fallback:
            error = self.fallback_reason
        return {"backend": self.backend_name, "available": available, "error": error, "fallback": self.fallback}

    def _probe(self) -> None:
        """Rate-limited live ping so status() notices outages and recoveries without other traffic."""
        now = self._now()
        if self._last_probe is not None and now - self._last_probe < self.probe_interval_seconds:
            return
        self._last_probe = now
        try:
            self._call("ping")
        except StoreUnavailableError:
            pass
        except Exception:  # a broken ping is not proof of an outage; never raise from status()
            logger.warning("Metadata store (%s) health probe raised", self.backend_name, exc_info=True)

    def add(self, document: DocumentMetadata) -> DocumentMetadata:
        return self._call("add", document)

    def list_all(self) -> list[DocumentMetadata]:
        return self._call("list_all")

    def list_active(self) -> list[DocumentMetadata]:
        return self._call("list_active")

    def get_by_id(self, document_id: str) -> DocumentMetadata | None:
        return self._call("get_by_id", document_id)

    def deactivate(self, document_id: str) -> bool:
        return self._call("deactivate", document_id)

    def ping(self) -> None:
        self._call("ping")


def _unavailable_factory(message: str) -> Callable[[], Any]:
    def factory() -> Any:
        raise StoreUnavailableError(message)

    return factory


def _json_store(settings: Any, *, fallback: bool = False, reason: str | None = None) -> ResilientMetadataStore:
    store = ResilientMetadataStore(
        lambda: MetadataRepository(settings.metadata_file),
        backend_name="json",
        retry_seconds=settings.metadata_store_retry_seconds,
        fallback=fallback,
        fallback_reason=reason,
    )
    store.connect()
    return store


def create_metadata_store(settings: Any) -> ResilientMetadataStore:
    """Build the metadata store selected by settings.

    Production never falls back to the local JSON file (Render's disk is ephemeral); it reports the store as
    unavailable instead. Development/test fall back to JSON with a WARNING and ``status()["fallback"] = True``.
    """
    if not settings.use_mongo_metadata_backend:
        return _json_store(settings)

    uri = settings.mongodb_uri.strip()
    if not uri:
        message = "MONGODB_URI is empty"
        if settings.is_production:
            logger.error("METADATA_BACKEND=mongo but %s; document lookups will fail until it is set", message)
            return ResilientMetadataStore(
                _unavailable_factory(message),
                backend_name="mongo",
                retry_seconds=settings.metadata_store_retry_seconds,
            )
        logger.warning("METADATA_BACKEND=mongo but %s; using local JSON metadata (development only)", message)
        return _json_store(settings, fallback=True, reason=message)

    def mongo_factory() -> MongoMetadataRepository:
        return MongoMetadataRepository(
            uri,
            settings.mongodb_database,
            settings.mongodb_collection,
            timeout_ms=settings.mongodb_timeout_ms,
        )

    store = ResilientMetadataStore(
        mongo_factory,
        backend_name="mongo",
        retry_seconds=settings.metadata_store_retry_seconds,
    )
    if settings.is_production:
        # Connect lazily on first use; failures are reported via status() and StoreUnavailableError.
        return store

    if store.connect():
        return store
    reason = f"MongoDB unavailable: {store.last_error}"
    logger.warning("%s; using local JSON metadata (development only)", reason)
    return _json_store(settings, fallback=True, reason=reason)


def create_request_log_store(settings: Any) -> Any:
    """Mongo request logs when the mongo backend is selected and reachable, else the JSON file. Never raises."""
    uri = settings.mongodb_uri.strip()
    if settings.use_mongo_metadata_backend and uri:
        try:
            return MongoRequestLogRepository(
                uri,
                settings.mongodb_database,
                settings.mongodb_request_log_collection,
                timeout_ms=settings.mongodb_timeout_ms,
            )
        except Exception as exc:
            logger.warning("MongoDB request logs unavailable (%s); using local JSON request log", describe_error(exc))
    try:
        return RequestLogRepository(settings.request_log_file, max_entries=settings.request_log_max_entries)
    except Exception as exc:
        logger.error("JSON request log unavailable (%s); request logging disabled", describe_error(exc))
        return NullRequestLogRepository()


class NullRequestLogRepository:
    """Last-resort request log that drops entries (used only when no log file can be created)."""

    backend_name = "disabled"

    def add(self, payload: dict[str, Any]) -> None:
        return None

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def latest_by_type(self, log_type: str, limit: int = 20) -> list[dict[str, Any]]:
        return []
