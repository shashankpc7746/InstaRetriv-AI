"""JSON-file metadata repository plus the shared, crash-safe JSON list file helper."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from app.schemas import DocumentMetadata

logger = logging.getLogger("instaretriv.repository")

_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}

_REPLACE_ATTEMPTS = 5


def _lock_for(path: Path) -> threading.RLock:
    """One re-entrant lock per resolved file path, shared by every instance in this process."""
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class JsonListFile:
    """A JSON file holding a list, with locking, atomic writes and corrupt-file quarantine.

    - Every read-modify-write must run inside ``with self.lock:``.
    - Writes go to a temp file in the same directory, are fsync'ed, then ``os.replace``d over the target,
      so readers never observe a partially written file.
    - A missing or empty file reads as ``[]``.
    - Invalid JSON (or JSON that is not a list) is moved to ``<name>.corrupt-<timestamp>``, logged at ERROR,
      and the store restarts from ``[]``.
    """

    def __init__(self, path: str | Path, label: str) -> None:
        self.path = Path(path)
        self.label = label
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = _lock_for(self.path)
        with self.lock:
            if not self.path.exists():
                self.write([])

    def read(self) -> list[Any]:
        with self.lock:
            try:
                raw_bytes = self.path.read_bytes()
            except FileNotFoundError:
                return []
            try:
                # UnicodeDecodeError is a ValueError: non-UTF-8 garbage is quarantined like bad JSON.
                raw = raw_bytes.decode("utf-8-sig")
                if not raw.strip():
                    return []
                data = json.loads(raw)
            except ValueError as exc:
                self._quarantine(f"invalid JSON: {exc}")
                return []
            if not isinstance(data, list):
                self._quarantine(f"expected a JSON list, found {type(data).__name__}")
                return []
            return data

    def write(self, items: Iterable[Any]) -> None:
        payload = json.dumps(list(items), indent=2, ensure_ascii=False, default=str)
        with self.lock:
            self._atomic_write(payload)

    def _atomic_write(self, payload: str) -> None:
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_with_retry(tmp_name)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _replace_with_retry(self, tmp_name: str) -> None:
        # On Windows os.replace can briefly fail while another process (antivirus, indexer) holds the target.
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp_name, self.path)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.02 * (2**attempt))

    def _quarantine(self, reason: str) -> None:
        target = self.path.with_name(f"{self.path.name}.corrupt-{_utc_stamp()}")
        try:
            os.replace(self.path, target)
        except OSError:
            try:
                shutil.copy2(self.path, target)
            except OSError:
                logger.exception("%s file %s is corrupt (%s) and could not be quarantined", self.label, self.path, reason)
                return
        logger.error(
            "%s file %s is corrupt (%s); moved it to %s and starting from an empty list",
            self.label,
            self.path,
            reason,
            target,
        )
        try:
            self._atomic_write("[]")
        except OSError:
            logger.exception("Could not recreate %s file %s after quarantine", self.label, self.path)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def sort_newest_first(documents: list[DocumentMetadata]) -> list[DocumentMetadata]:
    return sorted(documents, key=lambda doc: _as_aware(doc.uploaded_at), reverse=True)


def parse_documents(items: Iterable[Any], source: str) -> list[DocumentMetadata]:
    """Validate raw records, skipping (and logging) any that are not valid documents."""
    documents: list[DocumentMetadata] = []
    for item in items:
        try:
            documents.append(DocumentMetadata.model_validate(item))
        except ValidationError as exc:
            record_id = item.get("id") if isinstance(item, dict) else None
            logger.error("Skipping invalid document record %r in %s: %s", record_id, source, exc.errors()[:3])
    return documents


class MetadataRepository:
    """Document metadata stored in a local JSON file (development / single-instance use)."""

    backend_name = "json"

    def __init__(self, metadata_file: str) -> None:
        self.metadata_path = Path(metadata_file)
        self._file = JsonListFile(self.metadata_path, label="Metadata")

    def _documents(self) -> list[DocumentMetadata]:
        return parse_documents(self._file.read(), str(self.metadata_path))

    def add(self, document: DocumentMetadata) -> DocumentMetadata:
        record = document.model_dump(mode="json")
        with self._file.lock:
            # Operate on raw records so unrecognised entries are preserved untouched.
            items = [item for item in self._file.read() if not (isinstance(item, dict) and item.get("id") == document.id)]
            items.append(record)
            self._file.write(items)
        return document

    def list_all(self) -> list[DocumentMetadata]:
        return sort_newest_first(self._documents())

    def list_active(self) -> list[DocumentMetadata]:
        return [doc for doc in self.list_all() if doc.is_active]

    def get_by_id(self, document_id: str) -> DocumentMetadata | None:
        for document in self._documents():
            if document.id == document_id:
                return document
        return None

    def deactivate(self, document_id: str) -> bool:
        """Archive a document. True if it exists (idempotent), False if the id is unknown."""
        with self._file.lock:
            items = self._file.read()
            for item in items:
                if isinstance(item, dict) and item.get("id") == document_id:
                    if item.get("is_active", True) is not False:
                        item["is_active"] = False
                        self._file.write(items)
                    return True
        return False

    def ping(self) -> None:
        """Raise if the store cannot be read (OSError) — used by health checks."""
        with self._file.lock:
            if self.metadata_path.exists():
                self.metadata_path.read_bytes()
            else:
                self._file.write([])

    def status(self) -> dict[str, Any]:
        """Same shape as ResilientMetadataStore.status(), for callers handed a bare repository."""
        try:
            self.ping()
        except OSError as exc:
            return {"backend": self.backend_name, "available": False, "error": str(exc), "fallback": False}
        return {"backend": self.backend_name, "available": True, "error": None, "fallback": False}
