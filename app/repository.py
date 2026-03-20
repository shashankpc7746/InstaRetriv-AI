import json
from pathlib import Path

from app.schemas import DocumentMetadata


class MetadataRepository:
    def __init__(self, metadata_file: str) -> None:
        self.metadata_path = Path(metadata_file)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.metadata_path.exists():
            self.metadata_path.write_text("[]", encoding="utf-8")

    def _read_all(self) -> list[DocumentMetadata]:
        raw = self.metadata_path.read_text(encoding="utf-8").strip() or "[]"
        items = json.loads(raw)
        return [DocumentMetadata.model_validate(item) for item in items]

    def _write_all(self, documents: list[DocumentMetadata]) -> None:
        payload = [doc.model_dump(mode="json") for doc in documents]
        self.metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add(self, document: DocumentMetadata) -> DocumentMetadata:
        documents = self._read_all()
        documents.append(document)
        self._write_all(documents)
        return document

    def list_active(self) -> list[DocumentMetadata]:
        return [doc for doc in self._read_all() if doc.is_active]

    def get_by_id(self, document_id: str) -> DocumentMetadata | None:
        for document in self._read_all():
            if document.id == document_id:
                return document
        return None

