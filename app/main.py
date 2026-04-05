import logging
from collections import Counter, deque
from pathlib import Path
from uuid import uuid4

import requests
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

_RECENT_MESSAGE_SIDS_LIMIT = 1000
_recent_message_sids_queue: deque[str] = deque()
_recent_message_sids_set: set[str] = set()

_TERMINAL_DELIVERY_STATES = {"delivered", "read", "failed", "undelivered", "canceled"}


def _normalize_twilio_status(message_status: str, error_code: str | None) -> str:
    status = (message_status or "").strip().lower()
    if error_code:
        return "failed"

    status_map = {
        "queued": "queued",
        "accepted": "queued",
        "scheduled": "queued",
        "sending": "sending",
        "sent": "sent",
        "delivered": "delivered",
        "read": "read",
        "undelivered": "failed",
        "failed": "failed",
        "canceled": "canceled",
    }
    return status_map.get(status, "unknown")


def _delivery_stage_rank(normalized_state: str) -> int:
    stage_order = {
        "unknown": 0,
        "queued": 1,
        "sending": 2,
        "sent": 3,
        "delivered": 4,
        "read": 5,
        "failed": 6,
        "canceled": 6,
    }
    return stage_order.get(normalized_state, 0)


def _remember_message_sid(message_sid: str) -> None:
    sid = message_sid.strip()
    if not sid or sid in _recent_message_sids_set:
        return
    _recent_message_sids_queue.append(sid)
    _recent_message_sids_set.add(sid)

    while len(_recent_message_sids_queue) > _RECENT_MESSAGE_SIDS_LIMIT:
        evicted = _recent_message_sids_queue.popleft()
        _recent_message_sids_set.discard(evicted)


def _seed_recent_message_sids() -> None:
    for log_entry in request_logs.latest(limit=500):
        sid = str(log_entry.get("message_sid", "")).strip()
        if sid:
            _remember_message_sid(sid)


_seed_recent_message_sids()


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


def _resolve_best_retrievable_document(query: str) -> tuple[RetrievalResult, int]:
    """Find the best match while skipping stale local-file records."""
    candidates = repository.list_active()
    stale_count = 0

    while candidates:
        result = find_best_document(query, candidates)
        if not result.found or result.document is None:
            return RetrievalResult(found=False, score=0.0), stale_count

        if is_remote_storage_path(result.document.storage_path):
            return result, stale_count

        file_path = Path(result.document.storage_path)
        if file_path.exists():
            return result, stale_count

        repository.deactivate(result.document.id)
        stale_count += 1
        logger.warning(
            "Auto-archived stale metadata during match resolution: query=%s doc_id=%s",
            query,
            result.document.id,
        )
        candidates = [doc for doc in candidates if doc.id != result.document.id]

    return RetrievalResult(found=False, score=0.0), stale_count


def _is_remote_file_accessible(url: str) -> bool:
    try:
        response = requests.get(url, stream=True, timeout=8)
        status_code = response.status_code
        response.close()
        return status_code < 400
    except Exception:
        return False


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
    result, _stale_count = _resolve_best_retrievable_document(query)
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


@app.get("/logs/delivery")
def recent_delivery_logs(limit: int = 20) -> list[dict]:
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")

    webhook_logs = request_logs.latest_by_type(log_type="webhook", limit=2000)
    callback_logs = request_logs.latest_by_type(log_type="twilio-status-callback", limit=4000)

    latest_status_by_sid: dict[str, dict] = {}
    for callback_entry in reversed(callback_logs):
        sid = str(callback_entry.get("twilio_sid") or "").strip()
        if not sid:
            continue
        if sid not in latest_status_by_sid:
            latest_status_by_sid[sid] = callback_entry

    correlated_logs: list[dict] = []
    for entry in reversed(webhook_logs):
        sid = str(entry.get("twilio_sid") or "").strip()
        if not sid:
            continue

        callback = latest_status_by_sid.get(sid)
        correlated_logs.append(
            {
                "timestamp": entry.get("timestamp"),
                "request_id": entry.get("request_id"),
                "sender": entry.get("sender"),
                "query": entry.get("query"),
                "doc_id": entry.get("doc_id"),
                "found": entry.get("found"),
                "twilio_sid": sid,
                "delivery_status": callback.get("message_status") if callback else "unknown",
                "normalized_delivery_state": callback.get("normalized_state") if callback else "unknown",
                "status_timestamp": callback.get("timestamp") if callback else None,
                "error_code": callback.get("error_code") if callback else None,
                "error_message": callback.get("error_message") if callback else None,
            }
        )
        if len(correlated_logs) >= limit:
            break

    return list(reversed(correlated_logs))


@app.get("/logs/delivery/summary")
def delivery_summary(limit: int = 200) -> dict:
    if limit < 1 or limit > 2000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 2000")

    callback_logs = request_logs.latest_by_type(log_type="twilio-status-callback", limit=limit)
    latest_by_sid: dict[str, dict] = {}

    for entry in callback_logs:
        sid = str(entry.get("twilio_sid") or "").strip()
        if not sid:
            continue

        existing = latest_by_sid.get(sid)
        if existing is None:
            latest_by_sid[sid] = entry
            continue

        incoming_state = str(entry.get("normalized_state") or "unknown")
        existing_state = str(existing.get("normalized_state") or "unknown")

        if _delivery_stage_rank(incoming_state) >= _delivery_stage_rank(existing_state):
            latest_by_sid[sid] = entry

    state_counter = Counter(
        str(entry.get("normalized_state") or "unknown") for entry in latest_by_sid.values()
    )

    terminal_total = sum(
        count for state, count in state_counter.items() if state in _TERMINAL_DELIVERY_STATES
    )
    success_total = state_counter.get("delivered", 0) + state_counter.get("read", 0)
    failed_total = state_counter.get("failed", 0) + state_counter.get("canceled", 0)
    pending_total = len(latest_by_sid) - terminal_total
    success_rate = round((success_total / terminal_total) * 100, 2) if terminal_total > 0 else None
    failure_rate = round((failed_total / terminal_total) * 100, 2) if terminal_total > 0 else None

    return {
        "tracked_message_count": len(latest_by_sid),
        "terminal_message_count": terminal_total,
        "pending_message_count": pending_total,
        "successful_terminal_count": success_total,
        "failed_terminal_count": failed_total,
        "success_rate_percent": success_rate,
        "failure_rate_percent": failure_rate,
        "counts_by_state": dict(sorted(state_counter.items())),
    }


@app.post("/webhook", response_model=WebhookResponse)
async def whatsapp_webhook(request: Request) -> WebhookResponse:
    form = await request.form()
    body = str(form.get("Body", ""))
    sender = str(form.get("From", ""))
    inbound_message_sid = str(form.get("MessageSid") or form.get("SmsMessageSid") or "").strip()

    if inbound_message_sid and inbound_message_sid in _recent_message_sids_set:
        logger.info(
            "Duplicate Twilio webhook ignored: sender=%s message_sid=%s query=%s",
            sender,
            inbound_message_sid,
            body,
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
                "error": "duplicate-message-sid",
                "message_sid": inbound_message_sid,
            }
        )
        return WebhookResponse(message="Duplicate webhook ignored.")

    if inbound_message_sid:
        _remember_message_sid(inbound_message_sid)

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
                    "message_sid": inbound_message_sid,
                }
            )
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    if settings.authorized_senders_list and sender not in settings.authorized_senders_list:
        logger.warning("Unauthorized sender blocked: %s", sender)
        return WebhookResponse(message="Unauthorized sender.")

    result, stale_count = _resolve_best_retrievable_document(body)

    if result.found and result.document is not None:
        message = f"Document found: {result.document.file_name}."
        message_sid = None
        requires_link_fallback = result.document.file_type.lower() in {"pdf", "doc", "docx"}

        if not is_remote_storage_path(result.document.storage_path):
            file_path = Path(result.document.storage_path)
        else:
            file_path = None

        if file_path is not None and not file_path.exists():
            # This should rarely happen because stale items are filtered earlier.
            repository.deactivate(result.document.id)
            if whatsapp_sender.enabled:
                message_sid = whatsapp_sender.send_text(
                    to_number=sender,
                    body=(
                        f"I found metadata for {result.document.file_name}, but the stored file is no longer available. "
                        "Please re-upload this document."
                    ),
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
                    "error": "stored-file-missing-late-check",
                    "message_sid": inbound_message_sid,
                }
            )
            return WebhookResponse(message="Stored file missing. Please re-upload your document.")

        if whatsapp_sender.enabled and settings.public_base_url.strip():
            if is_remote_storage_path(result.document.storage_path):
                media_url = result.document.storage_path
            else:
                media_url = f"{settings.public_base_url.rstrip('/')}/files/{result.document.id}"

            if is_remote_storage_path(media_url) and not _is_remote_file_accessible(media_url):
                repository.deactivate(result.document.id)
                explain_msg = (
                    f"I found {result.document.file_name}, but its cloud link is not publicly accessible yet. "
                    "Please re-upload the file and ensure Cloudinary public delivery for documents is enabled."
                )
                if whatsapp_sender.enabled:
                    message_sid = whatsapp_sender.send_text(to_number=sender, body=explain_msg)
                request_logs.add(
                    {
                        "request_id": request.state.request_id,
                        "type": "webhook",
                        "sender": sender,
                        "query": body,
                        "found": False,
                        "doc_id": result.document.id,
                        "twilio_sid": message_sid,
                        "error": "remote-file-not-accessible",
                        "message_sid": inbound_message_sid,
                    }
                )
                return WebhookResponse(message="Cloud file link is not accessible. Please re-upload the document.")

            message_sid = whatsapp_sender.send_media(
                to_number=sender,
                body=f"Sharing: {result.document.file_name}",
                media_url=media_url,
            )
            if message_sid:
                message = "Document found and sent to your WhatsApp."
            else:
                if requires_link_fallback:
                    whatsapp_sender.send_text(
                        to_number=sender,
                        body=f"Media preview failed. Open your document here: {media_url}",
                    )
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
                "message_sid": inbound_message_sid,
            }
        )
        return WebhookResponse(
            message=message,
            matched_document_id=result.document.id,
        )

    if stale_count > 0:
        info_message = (
            "I found older entries for this request, but those files are no longer available. "
            "Please re-upload that document."
        )
        message_sid = None
        if whatsapp_sender.enabled:
            message_sid = whatsapp_sender.send_text(to_number=sender, body=info_message)

        logger.info(
            "Webhook no retrievable document after skipping stale entries: sender=%s query=%s stale_count=%s",
            sender,
            body,
            stale_count,
        )
        request_logs.add(
            {
                "request_id": request.state.request_id,
                "type": "webhook",
                "sender": sender,
                "query": body,
                "found": False,
                "doc_id": None,
                "twilio_sid": message_sid,
                "error": "no-retrievable-document-stale-only",
                "stale_count": stale_count,
                "message_sid": inbound_message_sid,
            }
        )
        return WebhookResponse(message="Stored files were missing. Please re-upload your document.")

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
            "message_sid": inbound_message_sid,
        }
    )
    return WebhookResponse(message="Document not found. Please refine your request.")


@app.post("/webhook/status")
async def twilio_status_callback(request: Request) -> dict[str, str]:
    form = await request.form()
    twilio_sid = str(form.get("MessageSid") or form.get("SmsSid") or "").strip()
    message_status = str(form.get("MessageStatus") or form.get("SmsStatus") or "").strip().lower()
    to_number = str(form.get("To") or "").strip()
    from_number = str(form.get("From") or "").strip()
    error_code = str(form.get("ErrorCode") or "").strip() or None
    error_message = str(form.get("ErrorMessage") or "").strip() or None
    normalized_state = _normalize_twilio_status(message_status=message_status, error_code=error_code)

    if settings.require_twilio_signature:
        signature = request.headers.get("X-Twilio-Signature", "")
        form_data = {key: str(value) for key, value in form.items()}
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
                "Invalid Twilio status callback signature blocked for request_id=%s url=%s token_count=%s",
                request.state.request_id,
                validation_url,
                len(auth_tokens),
            )
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    request_logs.add(
        {
            "request_id": request.state.request_id,
            "type": "twilio-status-callback",
            "twilio_sid": twilio_sid,
            "message_status": message_status,
            "normalized_state": normalized_state,
            "to": to_number,
            "from": from_number,
            "error_code": error_code,
            "error_message": error_message,
        }
    )

    logger.info(
        "Twilio status callback received: sid=%s status=%s error_code=%s",
        twilio_sid,
        normalized_state,
        error_code,
    )

    return {"message": "Status callback received"}
