"""MongoDB-backed document metadata repository."""

from __future__ import annotations

import logging
from typing import Any

from app.repository import parse_documents, sort_newest_first
from app.schemas import DocumentMetadata

logger = logging.getLogger("instaretriv.repository.mongo")

_PROJECTION = {"_id": 0}
# Legacy records may lack is_active; the schema default is True.
_ACTIVE_FILTER: dict[str, Any] = {"is_active": {"$ne": False}}


class MongoMetadataRepository:
    """Document metadata in a MongoDB collection.

    ``__init__`` creates the unique ``id`` index, so construction fails fast (within ``timeout_ms``)
    when the cluster is unreachable or the credentials are wrong.
    """

    backend_name = "mongo"

    def __init__(self, mongodb_uri: str, database_name: str, collection_name: str, timeout_ms: int = 5000) -> None:
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise RuntimeError("pymongo is required for the MongoDB metadata backend") from exc

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
            self._collection.create_index("id", unique=True)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover - best effort cleanup
            logger.debug("Ignoring error while closing MongoDB client", exc_info=True)

    def _find(self, query: dict[str, Any]) -> list[DocumentMetadata]:
        items = list(self._collection.find(query, _PROJECTION))
        # Sort in Python: uploaded_at is stored as an ISO string and lexical order is not reliable across formats.
        return sort_newest_first(parse_documents(items, "MongoDB collection"))

    def add(self, document: DocumentMetadata) -> DocumentMetadata:
        payload = document.model_dump(mode="json")
        self._collection.replace_one({"id": document.id}, payload, upsert=True)
        return document

    def list_all(self) -> list[DocumentMetadata]:
        return self._find({})

    def list_active(self) -> list[DocumentMetadata]:
        return self._find(_ACTIVE_FILTER)

    def get_by_id(self, document_id: str) -> DocumentMetadata | None:
        item = self._collection.find_one({"id": document_id}, _PROJECTION)
        if item is None:
            return None
        documents = parse_documents([item], "MongoDB collection")
        return documents[0] if documents else None

    def deactivate(self, document_id: str) -> bool:
        """Archive a document. True if it exists (idempotent), False if the id is unknown."""
        updated = self._collection.update_one(
            {"id": document_id, **_ACTIVE_FILTER},
            {"$set": {"is_active": False}},
        )
        if updated.modified_count > 0:
            return True
        existing = self._collection.find_one({"id": document_id}, {"_id": 1})
        return existing is not None

    def ping(self) -> None:
        self._client.admin.command("ping")
