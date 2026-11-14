"""Request/audit log stores (JSON file and MongoDB). Logging must never break a request: add() never raises."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.repository import JsonListFile

logger = logging.getLogger("instaretriv.request_logs")

_SECONDS_PER_DAY = 86400
# After a MongoDB failure, skip request-log I/O for this long instead of paying the full
# server-selection timeout on every request during an outage.
MONGO_LOG_BACKOFF_SECONDS = 30.0


def _build_entry(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    entry = dict(payload)
    entry["timestamp"] = now.isoformat()
    return entry


class RequestLogRepository:
    """Request logs in a local JSON file, trimmed to the newest ``max_entries``."""

    backend_name = "json"

    def __init__(self, log_file: str, max_entries: int = 2000) -> None:
        self.log_path = Path(log_file)
        self.max_entries = max(1, int(max_entries))
        self._file = JsonListFile(self.log_path, label="Request log")

    def _entries(self) -> list[dict[str, Any]]:
        return [item for item in self._file.read() if isinstance(item, dict)]

    def _readable_entries(self) -> list[dict[str, Any]]:
        try:
            return self._entries()
        except OSError:
            logger.exception("Failed to read request log file %s", self.log_path)
            return []

    def add(self, payload: dict[str, Any]) -> None:
        try:
            entry = _build_entry(payload, datetime.now(timezone.utc))
            with self._file.lock:
                logs = self._entries()
                logs.append(entry)
                self._file.write(logs[-self.max_entries :])
        except Exception:
            logger.exception("Failed to write request log entry (type=%s)", payload.get("type") if isinstance(payload, dict) else None)

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        """The last ``limit`` entries, oldest -> newest."""
        if limit <= 0:
            return []
        return self._readable_entries()[-limit:]

    def latest_by_type(self, log_type: str, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return [entry for entry in self._readable_entries() if entry.get("type") == log_type][-limit:]


class MongoRequestLogRepository:
    """Request logs in MongoDB with a TTL index on ``created_at`` for retention."""

    backend_name = "mongo"

    def __init__(
        self,
        mongodb_uri: str,
        database_name: str,
        collection_name: str,
        timeout_ms: int = 5000,
        retention_days: int = 30,
    ) -> None:
        try:
            from pymongo import ASCENDING, DESCENDING, MongoClient
            from pymongo.errors import OperationFailure
        except ImportError as exc:
            raise RuntimeError("pymongo is required for the MongoDB request log backend") from exc

        self._suspended_until = 0.0
        timeout = max(100, int(timeout_ms))
        self._client = MongoClient(
            mongodb_uri,
            serverSelectionTimeoutMS=timeout,
            connectTimeoutMS=timeout,
            socketTimeoutMS=timeout * 2,
            appname="instaretriv",
        )
        try:
            self._collection = self._client[database_name][collection_name]
            self._collection.create_index([("type", ASCENDING), ("created_at", DESCENDING)])
            try:
                self._collection.create_index(
                    "created_at",
                    expireAfterSeconds=max(1, int(retention_days)) * _SECONDS_PER_DAY,
                )
            except OperationFailure as exc:
                # An existing created_at index with different options; keep it rather than failing startup.
                logger.warning("Could not create request log TTL index (keeping existing index): %s", exc)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best effort cleanup
            logger.debug("Ignoring error while closing MongoDB client", exc_info=True)

    def _suspended(self) -> bool:
        return time.monotonic() < self._suspended_until

    def _suspend(self) -> None:
        self._suspended_until = time.monotonic() + MONGO_LOG_BACKOFF_SECONDS

    def add(self, payload: dict[str, Any]) -> None:
        if self._suspended():
            logger.warning(
                "MongoDB request log unavailable; dropping entry (type=%s)",
                payload.get("type") if isinstance(payload, dict) else None,
            )
            return
        try:
            now = datetime.now(timezone.utc)
            entry = _build_entry(payload, now)
            entry["created_at"] = now
            self._collection.insert_one(entry)
        except Exception:
            self._suspend()
            logger.exception("Failed to write request log entry to MongoDB")

    def _latest(self, query: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        if limit <= 0 or self._suspended():
            return []
        try:
            # _id (ObjectId) breaks ties between entries written within the same millisecond.
            cursor = (
                self._collection.find(query, {"_id": 0, "created_at": 0})
                .sort([("created_at", -1), ("_id", -1)])
                .limit(limit)
            )
            items = list(cursor)
        except Exception:
            self._suspend()
            logger.exception("Failed to read request logs from MongoDB")
            return []
        items.reverse()
        return items

    def latest(self, limit: int = 20) -> list[dict[str, Any]]:
        """The last ``limit`` entries, oldest -> newest."""
        return self._latest({}, limit)

    def latest_by_type(self, log_type: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._latest({"type": log_type}, limit)

