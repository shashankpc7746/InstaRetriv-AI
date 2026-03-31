from __future__ import annotations

from app.schemas import DocumentMetadata


class MongoMetadataRepository:
    def __init__(self, mongodb_uri: str, database_name: str, collection_name: str) -> None:
        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise RuntimeError("pymongo is required for MongoDB backend") from exc

        self._client = MongoClient(mongodb_uri)
        self._collection = self._client[database_name][collection_name]
        self._collection.create_index("id", unique=True)

    def add(self, document: DocumentMetadata) -> DocumentMetadata:
        payload = document.model_dump(mode="json")
        self._collection.replace_one({"id": document.id}, payload, upsert=True)
        return document

    def list_all(self) -> list[DocumentMetadata]:
        items = self._collection.find({}, {"_id": 0})
        return [DocumentMetadata.model_validate(item) for item in items]

    def list_active(self) -> list[DocumentMetadata]:
        items = self._collection.find({"is_active": True}, {"_id": 0})
        return [DocumentMetadata.model_validate(item) for item in items]

    def deactivate(self, document_id: str) -> bool:
        updated = self._collection.update_one(
            {"id": document_id, "is_active": True},
            {"$set": {"is_active": False}},
        )
        if updated.modified_count > 0:
            return True

        existing = self._collection.find_one({"id": document_id}, {"_id": 1})
        return existing is not None

    def get_by_id(self, document_id: str) -> DocumentMetadata | None:
        item = self._collection.find_one({"id": document_id}, {"_id": 0})
        if item is None:
            return None
        return DocumentMetadata.model_validate(item)
