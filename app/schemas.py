from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    file_name: str
    file_type: str
    doc_category: str
    tags: list[str]
    storage_path: str
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True


class UploadResponse(BaseModel):
    message: str
    document: DocumentMetadata


class RetrievalResult(BaseModel):
    found: bool
    document: Optional[DocumentMetadata] = None
    score: float = 0.0


class WebhookResponse(BaseModel):
    message: str
    matched_document_id: Optional[str] = None
