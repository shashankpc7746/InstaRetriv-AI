import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.request_log_repository import RequestLogRepository
from app.repository import MetadataRepository
from app.repository_mongo import MongoMetadataRepository
from app.schemas import DocumentMetadata, RetrievalResult, UploadResponse, WebhookResponse
from app.services.matcher import find_best_document
from app.services.storage import CloudinaryStorageService, LocalStorageService, is_remote_storage_path
from app.services.twilio_validation import is_valid_twilio_signature
from app.services.whatsapp import WhatsAppSender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("instaretriv")

app = FastAPI(title=settings.app_name)


def create_metadata_repository():
    if settings.use_mongo_metadata_backend:
        if settings.mongodb_uri.strip():
            try:
                return MongoMetadataRepository(
                    mongodb_uri=settings.mongodb_uri,
                    database_name=settings.mongodb_database,
                    collection_name=settings.mongodb_collection,
                )
            except Exception as exc:
                logger.warning("Mongo backend init failed, falling back to JSON repository: %s", str(exc))
        else:
            logger.warning("Mongo backend selected but MONGODB_URI is empty; using JSON repository")

    return MetadataRepository(settings.metadata_file)


repository = create_metadata_repository()
request_logs = RequestLogRepository(settings.request_log_file)


def create_storage_service():
    if settings.use_cloudinary_storage_backend:
        if settings.cloudinary_configured:
            try:
                return CloudinaryStorageService(
                    cloud_name=settings.cloudinary_cloud_name,
                    api_key=settings.cloudinary_api_key,
                    api_secret=settings.cloudinary_api_secret,
                )
            except Exception as exc:
                logger.warning("Cloudinary init failed, falling back to local storage: %s", str(exc))
        else:
            logger.warning("Cloudinary backend selected but credentials are missing; using local storage")
    return LocalStorageService(settings.upload_dir)


storage_service = create_storage_service()
whatsapp_sender = WhatsAppSender(
    account_sid=settings.twilio_account_sid,
    auth_token=settings.twilio_auth_token,
    sender=settings.twilio_whatsapp_from,
    retries=settings.twilio_send_retries,
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id

        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            logger.exception("Unhandled error on request_id=%s", request_id)
            request_logs.add(
                {
                    "request_id": request_id,
                    "type": "unhandled-error",
                    "path": str(request.url.path),
                    "method": request.method,
                    "error": str(exc),
                }
            )
            return JSONResponse(
                status_code=500,
                content={
                    "message": "Internal server error",
                    "request_id": request_id,
                },
                headers={"X-Request-ID": request_id},
            )


app.add_middleware(RequestIDMiddleware)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env}


@app.get("/")
def upload_form():
    """Simple HTML form to upload documents."""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>InstaRetriv AI - Upload Documents</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
            .container { background: #f5f5f5; padding: 30px; border-radius: 8px; }
            h1 { color: #333; }
            form { background: white; padding: 20px; border-radius: 8px; }
            input, textarea { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
            button { background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }
            button:hover { background: #0056b3; }
            .info { background: #e7f3ff; padding: 15px; margin: 20px 0; border-radius: 4px; }
            .section { margin: 30px 0; }
            a { color: #007bff; text-decoration: none; }
            a:hover { text-decoration: underline; }
            .link-list { background: white; padding: 15px; border-radius: 4px; margin: 10px 0; }
            code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📄 InstaRetriv AI - Document Manager</h1>
            
            <div class="section">
                <h2>Upload Document</h2>
                <form method="post" action="/upload" enctype="multipart/form-data">
                    <input type="file" name="file" required accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.webp">
                    <input type="text" name="doc_category" placeholder="Category (e.g., resume, certificate)" required>
                    <textarea name="tags" placeholder="Tags separated by commas (e.g., resume, pdf, work)" required></textarea>
                    <button type="submit">Upload Document</button>
                </form>
            </div>

            <div class="info">
                <strong>📝 Tips:</strong>
                <ul>
                    <li>Use <strong>clear, descriptive tags</strong> for better search results</li>
                    <li>Examples: "resume", "cover letter", "pan card", "aadhar", "invoice"</li>
                    <li>Tags are case-insensitive and support partial matching</li>
                </ul>
            </div>

            <div class="section">
                <h2>🔍 Admin Tools</h2>
                <div class="link-list">
                    <p><strong>View All Stored Documents:</strong></p>
                    <a href="/admin/documents"><code>GET /admin/documents</code></a>
                    <p style="margin-top: 10px; font-size: 14px;">See all documents in MongoDB with their tags and details</p>
                </div>
                
                <div class="link-list">
                    <p><strong>Test Search Query:</strong></p>
                    <input type="text" id="searchQuery" placeholder="Enter search query (e.g., pan, resume)" value="Pan">
                    <button onclick="testSearch()">Test Search</button>
                    <p style="margin-top: 10px; font-size: 14px;">See if your document matches the search query</p>
                </div>
            </div>

            <div class="section">
                <h2>🤖 WhatsApp Integration</h2>
                <div class="info">
                    <p>Send a WhatsApp message to your Twilio Sandbox with a query like:</p>
                    <ul>
                        <li>"send my resume"</li>
                        <li>"pan card"</li>
                        <li>"aadhar certificate"</li>
                    </ul>
                </div>
            </div>
        </div>

        <script>
        function testSearch() {
            const query = document.getElementById('searchQuery').value;
            if (!query.trim()) {
                alert('Please enter a search query');
                return;
            }
            window.location.href = '/admin/search?q=' + encodeURIComponent(query);
        }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/setup/status")
def setup_status() -> dict[str, bool]:
    return {
        "twilio_sid_set": bool(settings.twilio_account_sid.strip()),
        "twilio_auth_token_set": bool(settings.twilio_auth_token.strip()),
        "twilio_whatsapp_from_set": bool(settings.twilio_whatsapp_from.strip()),
        "public_base_url_set": bool(settings.public_base_url.strip()),
        "require_twilio_signature": settings.require_twilio_signature,
        "mongodb_uri_set": bool(settings.mongodb_uri.strip()),
        "mongo_backend_selected": settings.use_mongo_metadata_backend,
        "cloudinary_configured": settings.cloudinary_configured,
        "cloudinary_backend_selected": settings.use_cloudinary_storage_backend,
    }


@app.get("/admin/documents")
def list_all_documents() -> dict:
    """Debug endpoint: List all documents stored in MongoDB/JSON."""
    try:
        all_docs = repository.list_all()
        return {
            "total_count": len(all_docs),
            "backend": "mongo" if settings.use_mongo_metadata_backend else "json",
            "documents": [
                {
                    "id": doc.id,
                    "file_name": doc.file_name,
                    "doc_category": doc.doc_category,
                    "tags": doc.tags,
                    "is_active": doc.is_active,
                }
                for doc in all_docs
            ],
        }
    except Exception as e:
        logger.error("Error listing documents: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Error listing documents: {str(e)}")


@app.get("/admin/search")
def test_search(q: str = None) -> dict:
    """Debug endpoint: Test search query against all documents."""
    from app.services.matcher import find_best_document

    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required (e.g., /admin/search?q=pan)")

    query = q.strip().lower()

    try:
        all_docs = repository.list_active()
        result = find_best_document(query, all_docs)

        return {
            "query": query,
            "found": result.found,
            "total_documents_searched": len(all_docs),
            "matched_document": {
                "id": result.document.id,
                "file_name": result.document.file_name,
                "doc_category": result.document.doc_category,
                "tags": result.document.tags,
                "match_score": result.match_score,
            } if result.document else None,
            "all_documents": [
                {
                    "id": doc.id,
                    "file_name": doc.file_name,
                    "tags": doc.tags,
                }
                for doc in all_docs
            ],
        }
    except Exception as e:
        logger.error("Error searching documents: %s", str(e))
        raise HTTPException(status_code=500, detail=f"Error searching documents: {str(e)}")


@app.post("/upload", response_model=UploadResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_category: str = Form(...),
    tags: str = Form(...),
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required.")

    parsed_tags = [tag.strip().lower() for tag in tags.split(",") if tag.strip()]
    if not parsed_tags:
        raise HTTPException(status_code=400, detail="At least one tag is required.")

    extension = Path(file.filename).suffix.lower().lstrip(".")
    if settings.allowed_extensions_list and extension not in settings.allowed_extensions_list:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{extension}")

    storage_path = await storage_service.save(file)
    document = DocumentMetadata(
        file_name=file.filename,
        file_type=extension,
        doc_category=doc_category.strip().lower(),
        tags=parsed_tags,
        storage_path=storage_path,
    )

    repository.add(document)
    logger.info("Uploaded document: id=%s file=%s", document.id, document.file_name)
    request_logs.add(
        {
            "request_id": request.state.request_id,
            "type": "upload",
            "file_name": document.file_name,
            "doc_id": document.id,
            "tags": document.tags,
        }
    )

    return UploadResponse(message="Upload successful", document=document)


@app.get("/get-document", response_model=RetrievalResult)
def get_document(query: str, request: Request) -> RetrievalResult:
    documents = repository.list_active()
    result = find_best_document(query, documents)
    logger.info(
        "Retrieval query processed: query=%s found=%s score=%.2f",
        query,
        result.found,
        result.score,
    )
    request_logs.add(
        {
            "request_id": request.state.request_id,
            "type": "get-document",
            "query": query,
            "found": result.found,
            "doc_id": result.document.id if result.document else None,
            "score": result.score,
        }
    )
    return result


@app.get("/documents", response_model=list[DocumentMetadata])
def list_documents(active_only: bool = True) -> list[DocumentMetadata]:
    return repository.list_active() if active_only else repository.list_all()


@app.delete("/documents/{document_id}")
def archive_document(document_id: str, request: Request) -> dict[str, str]:
    archived = repository.deactivate(document_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Document not found")

    request_logs.add(
        {
            "request_id": request.state.request_id,
            "type": "archive-document",
            "doc_id": document_id,
        }
    )
    return {"message": "Document archived"}


@app.get("/files/{document_id}")
def serve_document_file(document_id: str):
    document = repository.get_by_id(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")

    if is_remote_storage_path(document.storage_path):
        return RedirectResponse(url=document.storage_path)

    file_path = Path(document.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stored file missing")

    return FileResponse(path=file_path, filename=document.file_name)


@app.get("/logs/recent")
def recent_logs(limit: int = 20) -> list[dict]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    return request_logs.latest(limit=limit)


@app.post("/webhook", response_model=WebhookResponse)
async def whatsapp_webhook(request: Request) -> WebhookResponse:
    form = await request.form()
    body = str(form.get("Body", ""))
    sender = str(form.get("From", ""))

    if settings.require_twilio_signature:
        signature = request.headers.get("X-Twilio-Signature", "")
        form_data = {key: str(value) for key, value in form.items()}
        # Twilio signs against the public URL; use PUBLIC_BASE_URL behind tunnels/proxies.
        validation_url = str(request.url)
        if settings.public_base_url.strip():
            validation_url = f"{settings.public_base_url.rstrip('/')}{request.url.path}"
            if request.url.query:
                validation_url = f"{validation_url}?{request.url.query}"

        auth_tokens = [settings.twilio_auth_token]
        if settings.twilio_secondary_auth_token.strip():
            auth_tokens.append(settings.twilio_secondary_auth_token)

        is_valid_signature = any(
            is_valid_twilio_signature(
                auth_token=auth_token,
                request_url=validation_url,
                form_data=form_data,
                signature=signature,
            )
            for auth_token in auth_tokens
        )
        if not is_valid_signature:
            logger.warning(
                "Invalid Twilio signature blocked for request_id=%s url=%s token_count=%s",
                request.state.request_id,
                validation_url,
                len(auth_tokens),
            )
            request_logs.add(
                {
                    "request_id": request.state.request_id,
                    "type": "webhook",
                    "sender": sender,
                    "query": body,
                    "found": False,
                    "doc_id": None,
                    "twilio_sid": None,
                    "error": "invalid-twilio-signature",
                }
            )
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    if settings.authorized_senders_list and sender not in settings.authorized_senders_list:
        logger.warning("Unauthorized sender blocked: %s", sender)
        return WebhookResponse(message="Unauthorized sender.")

    result = find_best_document(body, repository.list_active())

    if result.found and result.document is not None:
        message = f"Document found: {result.document.file_name}."
        message_sid = None

        if not is_remote_storage_path(result.document.storage_path):
            file_path = Path(result.document.storage_path)
        else:
            file_path = None

        if file_path is not None and not file_path.exists():
            # Render local disk is ephemeral; metadata can outlive file binaries across restarts.
            repository.deactivate(result.document.id)
            stale_message = (
                f"I found metadata for {result.document.file_name}, but the stored file is no longer available. "
                "Please re-upload this document."
            )
            if whatsapp_sender.enabled:
                message_sid = whatsapp_sender.send_text(to_number=sender, body=stale_message)

            logger.warning(
                "Webhook matched stale document metadata: sender=%s query=%s doc_id=%s",
                sender,
                body,
                result.document.id,
            )
            request_logs.add(
                {
                    "request_id": request.state.request_id,
                    "type": "webhook",
                    "sender": sender,
                    "query": body,
                    "found": False,
                    "doc_id": result.document.id,
                    "twilio_sid": message_sid,
                    "error": "stored-file-missing",
                }
            )
            return WebhookResponse(message="Stored file missing. Please re-upload your document.")

        if whatsapp_sender.enabled and settings.public_base_url.strip():
            if is_remote_storage_path(result.document.storage_path):
                media_url = result.document.storage_path
            else:
                media_url = f"{settings.public_base_url.rstrip('/')}/files/{result.document.id}"
            message_sid = whatsapp_sender.send_media(
                to_number=sender,
                body=f"Sharing: {result.document.file_name}",
                media_url=media_url,
            )
            if message_sid:
                message = "Document found and sent to your WhatsApp."
            else:
                message = "Document found but delivery failed. Please retry in a moment."
        elif whatsapp_sender.enabled:
            message_sid = whatsapp_sender.send_text(
                to_number=sender,
                body=f"Document found: {result.document.file_name}. Set PUBLIC_BASE_URL to enable file delivery.",
            )
            if message_sid:
                message = "Document found. Configure PUBLIC_BASE_URL to send files."
            else:
                message = "Document found but message delivery failed. Please retry."

        logger.info(
            "Webhook matched document: sender=%s query=%s doc_id=%s",
            sender,
            body,
            result.document.id,
        )
        request_logs.add(
            {
                "request_id": request.state.request_id,
                "type": "webhook",
                "sender": sender,
                "query": body,
                "found": True,
                "doc_id": result.document.id,
                "twilio_sid": message_sid,
            }
        )
        return WebhookResponse(
            message=message,
            matched_document_id=result.document.id,
        )

    logger.info("Webhook no document match: sender=%s query=%s", sender, body)
    request_logs.add(
        {
            "request_id": request.state.request_id,
            "type": "webhook",
            "sender": sender,
            "query": body,
            "found": False,
            "doc_id": None,
            "twilio_sid": None,
        }
    )
    return WebhookResponse(message="Document not found. Please refine your request.")
