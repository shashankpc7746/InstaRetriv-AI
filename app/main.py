import logging
import re
import secrets
import time
from hashlib import sha256
from collections import Counter, deque
from pathlib import Path
from uuid import uuid4

import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.profile_settings_repository import ProfileSettingsRepository
from app.request_log_repository import RequestLogRepository
from app.repository import MetadataRepository
from app.repository_mongo import MongoMetadataRepository
from app.schemas import DocumentMetadata, RetrievalResult, UploadResponse, WebhookResponse
from app.services.matcher import find_best_document
from app.services.storage import CloudinaryStorageService, LocalStorageService, is_remote_storage_path
from app.services.twilio_validation import is_valid_twilio_signature
from app.services.whatsapp import WhatsAppSender
from app.ui_templates import build_upload_page

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
profile_settings_repo = ProfileSettingsRepository(settings.profile_settings_file)

_RECENT_MESSAGE_SIDS_LIMIT = 1000
_recent_message_sids_queue: deque[str] = deque()
_recent_message_sids_set: set[str] = set()
_private_access_challenges: dict[str, dict[str, int | str]] = {}
_dashboard_sessions: dict[str, float] = {}

_DASHBOARD_SESSION_COOKIE = "instaretriv_session"
_PRIVATE_CONFIRM_REPLY = "yes"
_PRIVATE_CONFIRM_ALT_REPLY = "1"

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


def _extract_filename_tags(filename: str) -> list[str]:
    stem = Path(filename).stem.lower()
    if not stem:
        return []

    # Split on non-alphanumeric boundaries and remove noisy/common fragments.
    raw_parts = re.split(r"[^a-z0-9]+", stem)
    stop_words = {
        "final",
        "new",
        "copy",
        "scan",
        "image",
        "img",
        "document",
        "doc",
        "file",
        "latest",
        "updated",
        "version",
        "v",
    }

    extracted: list[str] = []
    for part in raw_parts:
        token = part.strip()
        if not token or len(token) < 2:
            continue
        if token in stop_words:
            continue
        if token.isdigit() and len(token) < 4:
            continue
        extracted.append(token)

    return extracted


def _merge_tags(manual_tags: list[str], filename: str) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    for tag in manual_tags + _extract_filename_tags(filename):
        normalized = tag.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)

    return merged


def _suggest_doc_category(tags: list[str]) -> tuple[str | None, float]:
    if not tags:
        return None, 0.0

    tokens = set(tags)
    category_keywords: dict[str, set[str]] = {
        "resume": {"resume", "cv", "curriculum", "vitae", "profile"},
        "cover-letter": {"cover", "letter", "motivation"},
        "certificate": {"certificate", "cert", "diploma", "transcript"},
        "id": {"aadhar", "aadhaar", "pan", "passport", "license", "id"},
        "invoice": {"invoice", "bill", "receipt", "gst", "payment"},
        "bank": {"bank", "statement", "passbook", "account", "ifsc"},
    }

    best_category = None
    best_match_count = 0
    for category, keywords in category_keywords.items():
        match_count = len(tokens & keywords)
        if match_count > best_match_count:
            best_match_count = match_count
            best_category = category

    if not best_category or best_match_count == 0:
        return None, 0.0

    # 1 strong keyword or 2 weak keyword matches make this a usable hint.
    confidence = min(1.0, best_match_count / 2.0)
    return best_category, round(confidence, 2)


def _derive_final_tags(manual_tags: list[str], filename: str, category: str, extension: str) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()

    def add_token(token: str) -> None:
        normalized = (token or "").strip().lower()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        merged.append(normalized)

    for token in manual_tags:
        add_token(token)

    for token in _extract_filename_tags(filename):
        add_token(token)

    if category and category not in {"general", "misc", "other", "document", "documents", "file"}:
        add_token(category)

    if not merged:
        add_token(extension)

    return merged


def _send_webhook_text_reply(sender: str, body: str) -> str | None:
    if not whatsapp_sender.enabled:
        logger.warning("WhatsApp sender is not enabled. Twilio creds may be missing.")
        return None
    if not sender.strip():
        return None
    return whatsapp_sender.send_text(to_number=sender, body=body)


def _is_private_confirmation_reply(body: str) -> bool:
    normalized = (body or "").strip().lower()
    return normalized in set(settings.private_confirmation_yes_keywords_list)


def _is_private_cancel_reply(body: str) -> bool:
    normalized = (body or "").strip().lower()
    return normalized in set(settings.private_confirmation_cancel_keywords_list)


def _private_confirmation_prompt_text(file_name: str) -> str:
    return (
        f"{file_name} is a private document. "
        f"Reply '{_PRIVATE_CONFIRM_REPLY.upper()}' or '{_PRIVATE_CONFIRM_ALT_REPLY}' within "
        f"{settings.private_confirmation_ttl_seconds} seconds to confirm. "
        "Reply 'CANCEL' to stop."
    )


def _private_pending_prompt_text(file_name: str) -> str:
    return (
        f"Pending private access for {file_name}. "
        f"Reply '{_PRIVATE_CONFIRM_REPLY.upper()}' or '{_PRIVATE_CONFIRM_ALT_REPLY}' within "
        f"{settings.private_confirmation_ttl_seconds} seconds to confirm. "
        "Reply 'CANCEL' to stop."
    )


def _sanitize_query_for_logs(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned

    cleaned = re.sub(
        r"(?:passcode|code|pin|otp)\s*[:\-]?\s*[a-zA-Z0-9]{4,16}",
        "[REDACTED_SECRET]",
        cleaned,
        flags=re.IGNORECASE,
    )
    if re.fullmatch(r"\d{4,10}", cleaned):
        return "[REDACTED_TOKEN]"
    return cleaned


def _dashboard_auth_enabled() -> bool:
    return settings.dashboard_auth_enabled


def _new_dashboard_session_token() -> str:
    return secrets.token_urlsafe(32)


def _is_dashboard_authenticated(request: Request) -> bool:
    if not _dashboard_auth_enabled():
        return True

    token = (request.cookies.get(_DASHBOARD_SESSION_COOKIE) or "").strip()
    if not token:
        return False

    issued_at = _dashboard_sessions.get(token)
    if not issued_at:
        return False

    ttl_seconds = max(1, settings.dashboard_session_ttl_minutes) * 60
    if (time.time() - issued_at) > ttl_seconds:
        _dashboard_sessions.pop(token, None)
        return False

    return True


def _require_dashboard_auth(request: Request) -> None:
    if not _is_dashboard_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")


def _hash_access_code(access_code: str) -> str:
    normalized = access_code.strip()
    return sha256(normalized.encode("utf-8")).hexdigest()


def _get_private_access_code_hash() -> str | None:
    return profile_settings_repo.get_private_access_code_hash()


def _private_access_code_configured() -> bool:
    return bool(_get_private_access_code_hash())


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


@app.get("/login")
def login_page(request: Request):
    if _is_dashboard_authenticated(request):
        return RedirectResponse(url="/")

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>InstaRetriv AI - Login</title>
        <style>
            body { font-family: Segoe UI, Arial, sans-serif; margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f5f7fb; }
            .card { width: min(92vw, 420px); background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px; box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08); }
            h1 { margin: 0 0 14px; font-size: 1.35rem; }
            p { color: #64748b; margin: 0 0 14px; }
            form { display: grid; gap: 10px; }
            input { width: 100%; box-sizing: border-box; padding: 12px; border: 1px solid #cbd5e1; border-radius: 8px; }
            button { border: none; border-radius: 8px; background: #0f62fe; color: white; font-weight: 700; padding: 11px 14px; cursor: pointer; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Login</h1>
            <p>Sign in to access the InstaRetriv dashboard.</p>
            <form method="post" action="/login">
                <input type="text" name="username" placeholder="Username" required>
                <input type="password" name="password" placeholder="Password" required>
                <button type="submit">Sign In</button>
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post("/login")
async def login_submit(
    username: str = Form(...),
    password: str = Form(...),
):
    if not _dashboard_auth_enabled():
        return RedirectResponse(url="/", status_code=303)

    if username.strip() != settings.dashboard_username or password != settings.dashboard_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _new_dashboard_session_token()
    _dashboard_sessions[token] = time.time()
    ttl_seconds = max(1, settings.dashboard_session_ttl_minutes) * 60

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=_DASHBOARD_SESSION_COOKIE,
        value=token,
        max_age=ttl_seconds,
        httponly=True,
        samesite="lax",
    )
    return response


@app.get("/logout")
def logout(request: Request):
    token = (request.cookies.get(_DASHBOARD_SESSION_COOKIE) or "").strip()
    if token:
        _dashboard_sessions.pop(token, None)

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(_DASHBOARD_SESSION_COOKIE)
    return response


@app.get("/")
def upload_form(request: Request):
    """Simple HTML form to upload documents."""
    if _dashboard_auth_enabled() and not _is_dashboard_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(content=build_upload_page())


@app.get("/setup/status")
def setup_status() -> dict[str, bool]:
    return {
        "twilio_sid_set": bool(settings.twilio_account_sid.strip()),
        "twilio_auth_token_set": bool(settings.twilio_auth_token.strip()),
        "twilio_whatsapp_from_set": bool(settings.twilio_whatsapp_from.strip()),
        "twilio_sender_enabled": whatsapp_sender.enabled,
        "private_access_code_set": _private_access_code_configured(),
        "dashboard_auth_enabled": _dashboard_auth_enabled(),
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
    doc_category: str = Form(""),
    tags: str = Form(""),
    is_private: bool = Form(False),
) -> UploadResponse:
    _require_dashboard_auth(request)

    if not file.filename:
        raise HTTPException(status_code=400, detail="File name is required.")

    parsed_tags = [tag.strip().lower() for tag in tags.split(",") if tag.strip()]
    input_category = doc_category.strip().lower()

    extension = Path(file.filename).suffix.lower().lstrip(".")
    if settings.allowed_extensions_list and extension not in settings.allowed_extensions_list:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{extension}")

    provisional_category = input_category or "general"
    final_tags = _derive_final_tags(parsed_tags, file.filename, provisional_category, extension)

    suggested_category, suggestion_confidence = _suggest_doc_category(final_tags)
    generic_categories = {"", "general", "misc", "other", "document", "documents", "file", "id"}

    final_category = input_category or "general"
    if (
        suggested_category
        and final_category in generic_categories
        and suggestion_confidence >= 0.5
    ):
        final_category = suggested_category

    if is_private:
        # Private documents are confirmed via short-lived WhatsApp "YES" step.
        pass

    storage_path = await storage_service.save(file)
    document = DocumentMetadata(
        file_name=file.filename,
        file_type=extension,
        doc_category=final_category,
        tags=final_tags,
        storage_path=storage_path,
        is_private=is_private,
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
            "doc_category_input": input_category,
            "doc_category_final": final_category,
            "doc_category_suggested": suggested_category,
            "doc_category_suggestion_confidence": suggestion_confidence,
            "is_private": is_private,
        }
    )

    return UploadResponse(message="Upload successful", document=document)


@app.post("/profile/private-access-code")
async def set_private_access_code(
    request: Request,
    private_access_code: str = Form(...),
    confirm_private_access_code: str = Form(...),
) -> dict[str, str]:
    _require_dashboard_auth(request)

    normalized = private_access_code.strip()
    confirm = confirm_private_access_code.strip()

    if len(normalized) < 4:
        raise HTTPException(status_code=400, detail="Private access code must be at least 4 characters.")
    if normalized != confirm:
        raise HTTPException(status_code=400, detail="Private access code and confirmation do not match.")

    profile_settings_repo.set_private_access_code_hash(_hash_access_code(normalized))
    request_logs.add(
        {
            "request_id": request.state.request_id,
            "type": "profile-security",
            "event": "private-access-code-updated",
        }
    )
    return {"message": "Private access code updated."}


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
def list_documents(request: Request, active_only: bool = True) -> list[DocumentMetadata]:
    _require_dashboard_auth(request)
    return repository.list_active() if active_only else repository.list_all()


@app.delete("/documents/{document_id}")
def archive_document(document_id: str, request: Request) -> dict[str, str]:
    _require_dashboard_auth(request)

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
def recent_logs(request: Request, limit: int = 20) -> list[dict]:
    _require_dashboard_auth(request)

    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    return request_logs.latest(limit=limit)


@app.get("/logs/delivery")
def recent_delivery_logs(request: Request, limit: int = 20) -> list[dict]:
    _require_dashboard_auth(request)

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
def delivery_summary(request: Request, limit: int = 200) -> dict:
    _require_dashboard_auth(request)

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
    safe_body = _sanitize_query_for_logs(body)
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
                "query": safe_body,
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
                    "query": safe_body,
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
        _send_webhook_text_reply(sender, "Unauthorized sender.")
        return WebhookResponse(message="Unauthorized sender.")

    is_confirmation_reply = _is_private_confirmation_reply(body)
    is_cancel_reply = _is_private_cancel_reply(body)
    result: RetrievalResult | None = None
    stale_count = 0
    sender_challenge = _private_access_challenges.get(sender)

    if sender and sender_challenge is None and (is_confirmation_reply or is_cancel_reply):
        request_logs.add(
            {
                "request_id": request.state.request_id,
                "type": "private-access-audit",
                "sender": sender,
                "doc_id": None,
                "status": "no-active-challenge",
                "reason": "confirmation-without-challenge",
                "message_sid": inbound_message_sid,
            }
        )
        _send_webhook_text_reply(sender, "No pending private confirmation. Please request a document first.")
        return WebhookResponse(message="No pending private confirmation. Please request a document first.")

    if sender and sender_challenge:
        challenged_doc_id = str(sender_challenge.get("doc_id") or "")
        expires_at = int(sender_challenge.get("expires_at") or 0)
        challenged_document = repository.get_by_id(challenged_doc_id) if challenged_doc_id else None
        now_ts = int(time.time())

        if challenged_document is None or not challenged_document.is_active or not challenged_document.is_private:
            _private_access_challenges.pop(sender, None)
        elif now_ts > expires_at:
            _private_access_challenges.pop(sender, None)
            request_logs.add(
                {
                    "request_id": request.state.request_id,
                    "type": "private-access-audit",
                    "sender": sender,
                    "doc_id": challenged_document.id,
                    "status": "challenge-expired",
                    "reason": "timeout",
                    "message_sid": inbound_message_sid,
                }
            )
            _send_webhook_text_reply(sender, "Private confirmation expired. Please request your private file again.")
            return WebhookResponse(message="Private confirmation expired. Please request your private file again.")
        elif is_cancel_reply:
            _private_access_challenges.pop(sender, None)
            request_logs.add(
                {
                    "request_id": request.state.request_id,
                    "type": "private-access-audit",
                    "sender": sender,
                    "doc_id": challenged_document.id,
                    "status": "challenge-canceled",
                    "reason": "user-cancel",
                    "message_sid": inbound_message_sid,
                }
            )
            _send_webhook_text_reply(sender, "Private request canceled.")
            return WebhookResponse(message="Private request canceled.")
        elif not is_confirmation_reply:
            reply_sid = _send_webhook_text_reply(sender, _private_pending_prompt_text(challenged_document.file_name))
            request_logs.add(
                {
                    "request_id": request.state.request_id,
                    "type": "private-access-audit",
                    "sender": sender,
                    "doc_id": challenged_document.id,
                    "status": "challenge-pending",
                    "reason": "confirmation-required",
                    "twilio_sid": reply_sid,
                    "message_sid": inbound_message_sid,
                }
            )
            return WebhookResponse(message=_private_pending_prompt_text(challenged_document.file_name))
        else:
            _private_access_challenges.pop(sender, None)
            request_logs.add(
                {
                    "request_id": request.state.request_id,
                    "type": "private-access-audit",
                    "sender": sender,
                    "doc_id": challenged_document.id,
                    "status": "access-granted",
                    "reason": "yes-confirmation",
                    "message_sid": inbound_message_sid,
                }
            )
            result = RetrievalResult(found=True, document=challenged_document, score=999.0)
            stale_count = 0

    if result is None:
        result, stale_count = _resolve_best_retrievable_document(body)

    if result.found and result.document is not None and result.document.is_private:
        if not is_confirmation_reply:
            if sender:
                _private_access_challenges[sender] = {
                    "doc_id": result.document.id,
                    "expires_at": int(time.time()) + max(15, settings.private_confirmation_ttl_seconds),
                }

            reply_sid = _send_webhook_text_reply(sender, _private_confirmation_prompt_text(result.document.file_name))

            request_logs.add(
                {
                    "request_id": request.state.request_id,
                    "type": "private-access-audit",
                    "sender": sender,
                    "doc_id": result.document.id,
                    "status": "challenge-issued",
                    "reason": "yes-confirmation-required",
                    "twilio_sid": reply_sid,
                    "message_sid": inbound_message_sid,
                }
            )
            return WebhookResponse(message=_private_confirmation_prompt_text(result.document.file_name))

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
                    "query": safe_body,
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
                        "query": safe_body,
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
                "query": safe_body,
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
                "query": safe_body,
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
    no_match_sid = _send_webhook_text_reply(sender, "Document not found. Please refine your request.")
    request_logs.add(
        {
            "request_id": request.state.request_id,
            "type": "webhook",
            "sender": sender,
            "query": safe_body,
            "found": False,
            "doc_id": None,
            "twilio_sid": no_match_sid,
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
