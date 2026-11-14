from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

StorageKind = Literal["local", "cloudinary", "url"]


class DocumentMetadata(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    file_name: str
    file_type: str
    doc_category: str
    tags: list[str]
    storage_path: str
    is_private: bool = False
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

    @field_validator("uploaded_at")
    @classmethod
    def _assume_utc_when_naive(cls, value: datetime) -> datetime:
        # Hand-edited / imported records may carry naive timestamps; comparing them with aware
        # ones would raise TypeError (e.g. in the matcher's newest-first tie-break).
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def storage_kind_for_path(storage_path: str) -> StorageKind:
    value = (storage_path or "").strip().lower()
    if value.startswith("cloudinary://"):
        return "cloudinary"
    if value.startswith(("http://", "https://")):
        return "url"
    return "local"


class DocumentPublic(BaseModel):
    """What the API exposes about a document: never the server path or storage URL."""

    id: str
    file_name: str
    file_type: str
    doc_category: str
    tags: list[str]
    is_private: bool = False
    uploaded_at: datetime
    is_active: bool = True
    storage_kind: StorageKind = "local"

    @classmethod
    def from_metadata(cls, document: DocumentMetadata) -> "DocumentPublic":
        data = document.model_dump(exclude={"storage_path"})
        return cls(**data, storage_kind=storage_kind_for_path(document.storage_path))


class UploadResponse(BaseModel):
    message: str
    document: DocumentPublic


class RetrievalResult(BaseModel):
    """Internal matcher result (holds the full metadata, including storage_path)."""

    found: bool
    document: Optional[DocumentMetadata] = None
    score: float = 0.0


class PublicRetrievalResult(BaseModel):
    """GET /get-document response: same shape as RetrievalResult, with a DocumentPublic."""

    found: bool
    document: Optional[DocumentPublic] = None
    score: float = 0.0

    @classmethod
    def from_result(cls, result: RetrievalResult) -> "PublicRetrievalResult":
        document = DocumentPublic.from_metadata(result.document) if result.document is not None else None
        return cls(found=result.found and document is not None, document=document, score=result.score)


class WebhookResponse(BaseModel):
    message: str
    matched_document_id: Optional[str] = None
