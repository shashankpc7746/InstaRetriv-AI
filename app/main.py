import logging
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from app.config import settings
from app.repository import MetadataRepository
from app.schemas import DocumentMetadata, RetrievalResult, UploadResponse, WebhookResponse
from app.services.matcher import find_best_document
from app.services.storage import LocalStorageService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("instaretriv")

app = FastAPI(title=settings.app_name)

repository = MetadataRepository(settings.metadata_file)
storage_service = LocalStorageService(settings.upload_dir)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@app.post("/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    doc_category: str = Form(...),
    tags: str = Form(...),
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required.")

    parsed_tags = [tag.strip().lower() for tag in tags.split(",") if tag.strip()]
    if not parsed_tags:
        raise HTTPException(status_code=400, detail="At least one tag is required.")

    storage_path = await storage_service.save(file)
    document = DocumentMetadata(
        file_name=file.filename,
        file_type=Path(file.filename).suffix.lower().lstrip("."),
        doc_category=doc_category.strip().lower(),
        tags=parsed_tags,
        storage_path=storage_path,
    )

    repository.add(document)
    logger.info("Uploaded document: id=%s file=%s", document.id, document.file_name)

    return UploadResponse(message="Upload successful", document=document)


@app.get("/get-document", response_model=RetrievalResult)
def get_document(query: str) -> RetrievalResult:
    documents = repository.list_active()
    result = find_best_document(query, documents)
    logger.info(
        "Retrieval query processed: query=%s found=%s score=%.2f",
        query,
        result.found,
        result.score,
    )
    return result


@app.post("/webhook", response_model=WebhookResponse)
def whatsapp_webhook(
    Body: str = Form(default=""),
    From: str = Form(default=""),
) -> WebhookResponse:
    if settings.authorized_senders_list and From not in settings.authorized_senders_list:
        logger.warning("Unauthorized sender blocked: %s", From)
        return WebhookResponse(message="Unauthorized sender.")

    result = find_best_document(Body, repository.list_active())

    if result.found and result.document is not None:
        logger.info(
            "Webhook matched document: sender=%s query=%s doc_id=%s",
            From,
            Body,
            result.document.id,
        )
        return WebhookResponse(
            message=f"Document found: {result.document.file_name}. Media sending will be enabled in next phase.",
            matched_document_id=result.document.id,
        )

    logger.info("Webhook no document match: sender=%s query=%s", From, Body)
    return WebhookResponse(message="Document not found. Please refine your request.")
