import hashlib
import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4
from xml.sax.saxutils import escape as xml_escape

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.middleware.base import BaseHTTPMiddleware

from app import security
from app.config import normalize_phone, settings
from app.schemas import (
    DocumentMetadata,
    DocumentPublic,
    PublicRetrievalResult,
    RetrievalResult,
    UploadResponse,
    WebhookResponse,
)
from app.security import LoginRateLimiter, get_app_secret, mask_phone, sanitize_text_for_logs
from app.services import tagging
from app.services.matcher import STOPWORDS, find_best_document, normalize_text, normalized_tokens
from app.services.storage import (
    CloudinaryStorageService,
    LocalStorageService,
    StorageUnavailableError,
    StoredFileNotFoundError,
    UploadRejectedError,
    guess_content_type,
    is_remote_storage_path,
)
from app.services.whatsapp import WhatsAppSender
from app.stores import StoreUnavailableError, create_metadata_store, create_request_log_store
from app.ui_templates import build_dashboard_page, build_login_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("instaretriv")


class _StripQueryStringFilter(logging.Filter):
    """Remove query strings from uvicorn access-log lines.

    Signed file links (?exp=..&sig=..) are bearer credentials for FILE_URL_TTL_SECONDS and
    /get-document?query=... can contain private text, so neither may reach the hosting logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn access records: (client_addr, method, full_path, http_version, status_code)
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0] + "?[redacted]", *args[3:])
        return True


_uvicorn_access_logger = logging.getLogger("uvicorn.access")
if not any(isinstance(existing, _StripQueryStringFilter) for existing in _uvicorn_access_logger.filters):
    _uvicorn_access_logger.addFilter(_StripQueryStringFilter())


def _api_docs_urls(app_settings: Any) -> dict[str, str | None]:
    """/docs, /redoc and /openapi.json only when enabled (off in production by default)."""
    if app_settings.api_docs_enabled:
        return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


app = FastAPI(title=settings.app_name, **_api_docs_urls(settings))


# --------------------------------------------------------------------------- service wiring


class UnavailableStorageService(LocalStorageService):
    """Production stand-in when the selected remote storage cannot be used.

    Uploads fail loudly (503) instead of silently landing on the ephemeral local disk;
    legacy local files and public URLs can still be read.
    """

    backend_name = "unavailable"

    def __init__(self, reason: str, upload_dir: str, max_bytes: int) -> None:
        super().__init__(upload_dir, max_bytes=max_bytes)
        self.reason = reason

    async def save(self, upload_file: UploadFile) -> str:
        raise StorageUnavailableError(self.reason)


# Why the selected storage backend is not the active one (None when it is).
storage_fallback_reason: str | None = None


def create_storage_service(app_settings: Any = settings) -> Any:
    global storage_fallback_reason
    storage_fallback_reason = None
    max_bytes = app_settings.max_upload_bytes

    if app_settings.use_cloudinary_storage_backend:
        reason = "Cloudinary is not configured"
        if app_settings.cloudinary_configured:
            try:
                return CloudinaryStorageService(
                    cloud_name=app_settings.cloudinary_cloud_name,
                    api_key=app_settings.cloudinary_api_key,
                    api_secret=app_settings.cloudinary_api_secret,
                    max_bytes=max_bytes,
                )
            except Exception as exc:  # StorageUnavailableError, missing package, bad config
                reason = f"Cloudinary could not be initialised: {str(exc)[:200]}"
        storage_fallback_reason = reason
        if app_settings.is_production:
            logger.error("STORAGE_BACKEND=cloudinary but %s; uploads are disabled until it is fixed", reason)
            return UnavailableStorageService(reason, app_settings.upload_dir, max_bytes)
        logger.warning("STORAGE_BACKEND=cloudinary but %s; using local disk storage (development only)", reason)

    return LocalStorageService(app_settings.upload_dir, max_bytes=max_bytes)


repository = create_metadata_store(settings)
request_logs = create_request_log_store(settings)
storage_service = create_storage_service(settings)
whatsapp_sender = WhatsAppSender(
    account_sid=settings.twilio_account_sid,
    auth_token=settings.twilio_auth_token,
    sender=settings.twilio_whatsapp_from,
    retries=settings.twilio_send_retries,
)
login_rate_limiter = LoginRateLimiter(
    max_attempts=settings.login_max_attempts,
    lockout_seconds=max(1, settings.login_lockout_minutes) * 60,
)
# Account-wide ceiling on failed sign-ins from ALL clients, so rotating IP addresses (or spoofed
# X-Forwarded-For values) cannot buy unlimited password guesses.
login_account_rate_limiter = LoginRateLimiter(
    max_attempts=max(20, settings.login_max_attempts * 4),
    lockout_seconds=max(1, settings.login_lockout_minutes) * 60,
)
_LOGIN_ACCOUNT_KEY = "account"

_RECENT_MESSAGE_SIDS_LIMIT = 1000
_recent_message_sids_queue: deque[str] = deque()
_recent_message_sids_set: set[str] = set()
# normalized sender ('+<digits>') -> {"doc_id": str, "expires_at": float}
_private_access_challenges: dict[str, dict[str, Any]] = {}
# sha256 digest of a revoked dashboard session token -> unix time after which it would have expired
# anyway. Logouts are also written to the request log and re-loaded at start-up (survive restarts).
_revoked_session_tokens: dict[str, float] = {}
_revoked_sessions_lock = threading.Lock()

_DASHBOARD_SESSION_COOKIE = "instaretriv_session"
_MAX_TAGS = 20
_MAX_TAG_LENGTH = 40
_MAX_CATEGORY_LENGTH = 40
_MAX_FILE_NAME_LENGTH = 200
_NO_MATCH_HINT_WORDS = 5

# Request body caps, enforced before any body is read or spooled to disk.
_FORM_BODY_LIMIT_BYTES = 64 * 1024
_UPLOAD_BODY_OVERHEAD_BYTES = 256 * 1024
_TWILIO_FORM_PATHS = {"/webhook", "/webhook/status"}

# Reverse proxies in front of the app that append the client address to X-Forwarded-For
# (Render's edge appends one). Everything to the left of those entries is client-controlled.
_TRUSTED_PROXY_HOPS = 1

STORE_UNAVAILABLE_REPLY = (
    "⚠️ InstaRetriv can't reach its document database right now. Please try again in a few minutes."
)
STORE_UNAVAILABLE_DETAIL = "The document database is unavailable right now. Please try again in a few minutes."

_TERMINAL_DELIVERY_STATES = {"delivered", "read", "failed", "undelivered", "canceled"}

_HTML_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "Cache-Control": "no-store",
}


def _now() -> float:
    """Wall-clock time (a function so tests can control it)."""
    return time.time()


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


# --------------------------------------------------------------------------- inbound MessageSid dedup


def _remember_message_sid(message_sid: str) -> None:
    sid = message_sid.strip()
    if not sid or sid in _recent_message_sids_set:
        return
    _recent_message_sids_queue.append(sid)
    _recent_message_sids_set.add(sid)

    while len(_recent_message_sids_queue) > _RECENT_MESSAGE_SIDS_LIMIT:
        evicted = _recent_message_sids_queue.popleft()
        _recent_message_sids_set.discard(evicted)


def _forget_message_sid(message_sid: str) -> None:
    """Undo _remember_message_sid so Twilio's retry of a failed request is processed."""
    sid = message_sid.strip()
    if not sid or sid not in _recent_message_sids_set:
        return
    _recent_message_sids_set.discard(sid)
    try:
        _recent_message_sids_queue.remove(sid)
    except ValueError:
        pass


def _seed_recent_message_sids() -> None:
    """Pre-load SIDs of messages processed before a restart. Never fails start-up."""
    try:
        entries = request_logs.latest(limit=500)
    except Exception:
        logger.warning("Could not seed recent MessageSids from the request log", exc_info=True)
        return
    for log_entry in entries:
        if not isinstance(log_entry, dict):
            continue
        sid = str(log_entry.get("message_sid") or "").strip()
        if sid:
            _remember_message_sid(sid)


_seed_recent_message_sids()


# --------------------------------------------------------------------------- small helpers


def _first_header_value(value: str | None) -> str:
    if not value:
        return ""
    return value.split(",", 1)[0].strip()


def _client_ip(request: Request) -> str:
    """Client address for rate limiting.

    Only the X-Forwarded-For entries appended by our own proxies are trusted (the right-most
    ``_TRUSTED_PROXY_HOPS``); the left-most value is whatever the client chose to send.
    """
    hops = [hop.strip() for hop in (request.headers.get("x-forwarded-for") or "").split(",") if hop.strip()]
    if hops:
        return hops[-min(_TRUSTED_PROXY_HOPS, len(hops))]
    return request.client.host if request.client else "unknown"


def _request_is_https(request: Request) -> bool:
    proto = _first_header_value(request.headers.get("x-forwarded-proto")).lower()
    return proto == "https" or request.url.scheme == "https"


def _external_base_url(request: Request) -> str:
    """Base URL Twilio / browsers can reach: PUBLIC_BASE_URL, else derived from proxy headers."""
    configured = settings.public_base_url.strip().rstrip("/")
    if configured:
        return configured
    proto = _first_header_value(request.headers.get("x-forwarded-proto")).lower() or request.url.scheme
    host = (
        _first_header_value(request.headers.get("x-forwarded-host"))
        or request.headers.get("host", "").strip()
        or request.url.netloc
    )
    return f"{proto}://{host}".rstrip("/")


def _log_sender(value: Any) -> str:
    """Mask a phone number for logs/API output; values that are already masked are kept."""
    text = str(value or "")
    if not text or "*" in text:
        return text
    return mask_phone(text)


def _present_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    presented = dict(entry)
    for key in ("sender", "to", "from"):
        if presented.get(key):
            presented[key] = _log_sender(presented[key])
    return presented


def _content_disposition(file_name: str) -> str:
    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in file_name) or "document"
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{quote(file_name, safe='')}"


def _metadata_store_status() -> dict[str, Any]:
    status_fn = getattr(repository, "status", None)
    try:
        if callable(status_fn):
            status = dict(status_fn())
        else:
            repository.ping()
            status = {"backend": getattr(repository, "backend_name", "unknown"), "available": True, "error": None}
    except Exception as exc:
        status = {
            "backend": getattr(repository, "backend_name", "unknown"),
            "available": False,
            "error": str(exc)[:300],
        }
    status.setdefault("fallback", False)
    return status


# --------------------------------------------------------------------------- dashboard auth


def _dashboard_auth_enabled() -> bool:
    return settings.dashboard_auth_enabled


def _session_ttl_seconds() -> int:
    return max(1, settings.dashboard_session_ttl_minutes) * 60


def _session_secret() -> str:
    """Session signing key: APP_SECRET_KEY plus a fingerprint of the dashboard credentials.

    Changing DASHBOARD_USERNAME or DASHBOARD_PASSWORD therefore signs out every existing session.
    """
    credentials = f"{settings.dashboard_username}\x00{settings.dashboard_password}"
    fingerprint = hashlib.sha256(credentials.encode("utf-8")).hexdigest()
    return f"{get_app_secret()}|{fingerprint}"


def _session_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_session_revoked(token: str) -> bool:
    with _revoked_sessions_lock:
        return _session_digest(token) in _revoked_session_tokens


def _remember_revoked_session(digest: str, revoked_until: float) -> None:
    now = _now()
    with _revoked_sessions_lock:
        for revoked, until in list(_revoked_session_tokens.items()):
            if until < now:
                del _revoked_session_tokens[revoked]
        if revoked_until >= now:
            _revoked_session_tokens[digest] = revoked_until


def _revoke_session(token: str) -> None:
    digest = _session_digest(token)
    revoked_until = _now() + _session_ttl_seconds() + security.MAX_CLOCK_SKEW_SECONDS
    _remember_revoked_session(digest, revoked_until)
    # Persist the logout (digest only, never the token) so it still holds after a restart.
    request_logs.add({"type": "logout", "session_digest": digest, "revoked_until": revoked_until})


def _seed_revoked_sessions() -> None:
    """Re-load logouts recorded before a restart. Never fails start-up."""
    try:
        entries = request_logs.latest_by_type("logout", limit=500)
    except Exception:
        logger.warning("Could not load revoked dashboard sessions from the request log", exc_info=True)
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        digest = str(entry.get("session_digest") or "").strip()
        try:
            revoked_until = float(entry.get("revoked_until") or 0)
        except (TypeError, ValueError):
            continue
        if digest:
            _remember_revoked_session(digest, revoked_until)


_seed_revoked_sessions()


def _has_valid_session(request: Request) -> bool:
    token = (request.cookies.get(_DASHBOARD_SESSION_COOKIE) or "").strip()
    if not token or _is_session_revoked(token):
        return False
    return security.validate_session_token(token, _session_secret(), _session_ttl_seconds(), now=_now())


def _is_dashboard_authenticated(request: Request) -> bool:
    if not _dashboard_auth_enabled():
        return True
    return _has_valid_session(request)


def _require_dashboard_auth(request: Request) -> None:
    if not _is_dashboard_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")


def _session_cookie_secure(request: Request) -> bool:
    return settings.is_production or _request_is_https(request)


def _login_html(error: str | None, status_code: int, username_hint: str = "", headers: dict | None = None) -> HTMLResponse:
    content = build_login_page(
        error=error,
        login_available=settings.dashboard_login_available,
        username_hint=username_hint,
    )
    return HTMLResponse(content=content, status_code=status_code, headers={**_HTML_SECURITY_HEADERS, **(headers or {})})


# --------------------------------------------------------------------------- middleware / handlers


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception as exc:
            try:
                logger.exception("Unhandled error on request_id=%s path=%s", request_id, request.url.path)
                request_logs.add(
                    {
                        "request_id": request_id,
                        "type": "unhandled-error",
                        "path": str(request.url.path),
                        "method": request.method,
                        "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                    }
                )
            except Exception:  # logging must never prevent the 500 response
                pass
            return JSONResponse(
                status_code=500,
                content={"message": "Internal server error", "request_id": request_id},
                headers={"X-Request-ID": request_id},
            )

        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


class _BodyTooLargeError(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body is too large.")


def _body_limit_bytes(path: str) -> int:
    if path == "/upload":
        return settings.max_upload_bytes + _UPLOAD_BODY_OVERHEAD_BYTES
    return _FORM_BODY_LIMIT_BYTES


class BodyLimitMiddleware:
    """Pure ASGI middleware that runs before routing and before any body is read.

    - Rejects bodies larger than the per-path cap (413), by Content-Length and while streaming
      (chunked bodies), so anonymous clients cannot fill the disk through multipart spooling.
    - Rejects unauthenticated uploads (401) before the multipart body is parsed.
    - The Twilio endpoints only accept url-encoded forms (415 otherwise).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "").upper()
        headers = Headers(scope=scope)
        limit = _body_limit_bytes(path)

        rejection = self._early_rejection(scope, path, method, headers, limit)
        if rejection is not None:
            await rejection(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> dict:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    # An HTTPException, so FastAPI's body parsing re-raises it unchanged (-> 413).
                    raise _BodyTooLargeError()
            return message

        await self.app(scope, limited_receive, send)

    @staticmethod
    def _early_rejection(scope: dict, path: str, method: str, headers: Headers, limit: int) -> JSONResponse | None:
        declared = headers.get("content-length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
            if declared_bytes > limit:
                return JSONResponse(status_code=413, content={"detail": "Request body is too large."})

        if method == "POST" and path == "/upload" and not _is_dashboard_authenticated(Request(scope)):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})

        if method == "POST" and path in _TWILIO_FORM_PATHS:
            content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type and content_type != "application/x-www-form-urlencoded":
                return JSONResponse(status_code=415, content={"detail": "Unsupported content type"})
        return None


# Added before RequestIDMiddleware, so it runs inside it and early rejections still get request ids.
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(RequestIDMiddleware)


@app.exception_handler(StoreUnavailableError)
async def _store_unavailable_handler(request: Request, exc: StoreUnavailableError) -> JSONResponse:
    logger.error("Metadata store unavailable on %s: %s", request.url.path, str(exc)[:200])
    return JSONResponse(status_code=503, content={"detail": STORE_UNAVAILABLE_DETAIL})


async def _log_request_async(entry: dict[str, Any]) -> None:
    """request_logs.add off the event loop (the Mongo log store does network I/O)."""
    await run_in_threadpool(request_logs.add, entry)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid4())


# --------------------------------------------------------------------------- health / setup


def _health_payload() -> tuple[dict[str, Any], bool]:
    """(payload, ready). Readiness depends on the metadata store only: when file storage is unusable the
    app is "degraded" (uploads fail with 503) but existing documents can still be retrieved."""
    store_ok = bool(_metadata_store_status().get("available"))
    storage_ok = str(getattr(storage_service, "backend_name", "local")) != "unavailable"
    payload = {
        "status": "ok" if store_ok and storage_ok else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "checks": {
            "metadata_store": "ok" if store_ok else "unavailable",
            "storage": "ok" if storage_ok else "unavailable",
        },
    }
    return payload, store_ok


@app.get("/health")
def health() -> dict[str, Any]:
    payload, _ = _health_payload()
    return payload


@app.get("/ready")
def ready() -> JSONResponse:
    payload, store_ok = _health_payload()
    return JSONResponse(status_code=200 if store_ok else 503, content=payload)


def _sender_allowlist_enforced() -> bool:
    return bool(settings.authorized_senders_list) or (settings.is_production and not settings.allow_any_sender)


def _short_error(error: Any, max_len: int = 160) -> str | None:
    """First clause of a (possibly very long, e.g. pymongo topology) error message, cut at a word boundary."""
    if not error:
        return None
    text = " ".join(str(error).split())
    for separator in (", Timeout:", ", Topology Description:", "\n"):
        text = text.split(separator, 1)[0]
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + "…"
    return text.rstrip(" .,:;")


def _setup_warnings(store_status: dict[str, Any], storage_backend: str) -> list[str]:
    warnings: list[str] = []
    store_backend = store_status.get("backend") or "metadata"
    store_error = _short_error(store_status.get("error"))

    if not store_status.get("available"):
        warnings.append(
            f"The document database ({store_backend}) is unavailable: {store_error or 'unknown error'}. "
            "WhatsApp lookups and uploads will fail until it is fixed (check MONGODB_URI and that the cluster exists)."
        )
    elif store_status.get("fallback"):
        warnings.append(
            f"MongoDB is selected but not usable ({store_error or 'unknown error'}), so a temporary local JSON file "
            "is being used. Documents saved now will not be in your main database (development only)."
        )
    elif settings.is_production and not settings.use_mongo_metadata_backend:
        warnings.append(
            "METADATA_BACKEND=json in production: document records live on this server's disk, which hosting "
            "platforms like Render wipe on every deploy. Use METADATA_BACKEND=mongo."
        )

    if storage_backend == "unavailable":
        warnings.append(
            f"Cloudinary storage is selected but unavailable ({storage_fallback_reason or 'unknown error'}): "
            "uploads are disabled until it is fixed."
        )
    elif settings.use_cloudinary_storage_backend and storage_backend != "cloudinary":
        warnings.append(
            f"Cloudinary storage is selected but unavailable ({storage_fallback_reason or 'unknown error'}): "
            "files are being saved on this server's disk instead (development only)."
        )
    elif settings.is_production and storage_backend == "local":
        warnings.append(
            "Files are stored on this server's disk in production; hosting platforms like Render wipe it on "
            "every deploy. Use STORAGE_BACKEND=cloudinary."
        )

    request_log_backend = str(getattr(request_logs, "backend_name", "json"))
    if settings.use_mongo_metadata_backend and request_log_backend != "mongo":
        warnings.append(
            f"Request logs are kept in a local {request_log_backend} store because MongoDB was unreachable at "
            "startup, so activity and delivery history is lost on every restart. Restart the app once MongoDB "
            "is reachable."
        )

    if not settings.app_secret_key.strip():
        warnings.append(
            "APP_SECRET_KEY is not set: dashboard sessions and WhatsApp file links stop working after every restart."
        )

    if not _dashboard_auth_enabled():
        warnings.append(
            "Dashboard sign-in is turned off (DASHBOARD_AUTH_ENABLED=false): anyone who can reach this server can "
            "see, download and manage your documents."
        )
    elif not settings.dashboard_login_available:
        warnings.append("DASHBOARD_PASSWORD is not set, so dashboard sign-in is disabled.")

    if not settings.authorized_senders_list:
        if settings.is_production and not settings.allow_any_sender:
            warnings.append(
                "AUTHORIZED_SENDERS is empty: every WhatsApp number is blocked in production. Add your number(s), "
                "e.g. whatsapp:+919876543210."
            )
        elif settings.allow_any_sender:
            warnings.append("ALLOW_ANY_SENDER=true: any WhatsApp number can retrieve your documents.")
        else:
            warnings.append(
                "AUTHORIZED_SENDERS is empty: any WhatsApp number can use the bot (allowed only outside production)."
            )

    if not settings.public_base_url.strip():
        warnings.append(
            "PUBLIC_BASE_URL is not set: file links fall back to the request's host, which Twilio may not be able "
            "to reach. Set it to your public https URL."
        )
    elif settings.is_production and not settings.public_base_url.strip().lower().startswith("https://"):
        warnings.append(
            "PUBLIC_BASE_URL does not start with https://: Twilio signs and fetches over https, so webhook "
            "signature checks and file delivery will fail. Use your public https URL."
        )

    if not whatsapp_sender.enabled:
        detail = f" ({whatsapp_sender.last_error})" if getattr(whatsapp_sender, "last_error", None) else ""
        warnings.append(
            "WhatsApp sending is off: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM"
            f"{detail}. Replies are only returned to Twilio as plain text."
        )

    if settings.twilio_signature_required:
        if not (settings.twilio_auth_token.strip() or settings.twilio_secondary_auth_token.strip()):
            warnings.append(
                "Twilio signature checking is on but TWILIO_AUTH_TOKEN is empty: every webhook request will be rejected."
            )
    else:
        warnings.append(
            "Twilio signature checking is off: anyone who knows the /webhook URL can send fake messages. "
            "Set REQUIRE_TWILIO_SIGNATURE=true."
        )
    return warnings


@app.get("/setup/status")
def setup_status(request: Request) -> dict[str, Any]:
    _require_dashboard_auth(request)
    store_status = _metadata_store_status()
    storage_backend = str(getattr(storage_service, "backend_name", "local"))
    return {
        "twilio_sid_set": bool(settings.twilio_account_sid.strip()),
        "twilio_auth_token_set": bool(settings.twilio_auth_token.strip()),
        "twilio_whatsapp_from_set": bool(settings.twilio_whatsapp_from.strip()),
        "twilio_sender_enabled": whatsapp_sender.enabled,
        "dashboard_auth_enabled": _dashboard_auth_enabled(),
        "dashboard_login_available": settings.dashboard_login_available,
        "public_base_url_set": bool(settings.public_base_url.strip()),
        "require_twilio_signature": settings.require_twilio_signature,
        "twilio_signature_required": settings.twilio_signature_required,
        "mongodb_uri_set": bool(settings.mongodb_uri.strip()),
        "mongo_backend_selected": settings.use_mongo_metadata_backend,
        "cloudinary_configured": settings.cloudinary_configured,
        "cloudinary_backend_selected": settings.use_cloudinary_storage_backend,
        "app_env": settings.app_env,
        "app_secret_configured": bool(settings.app_secret_key.strip()),
        "metadata_store": store_status,
        "storage_backend_active": storage_backend,
        "request_log_backend": str(getattr(request_logs, "backend_name", "json")),
        "allowed_extensions": settings.allowed_extensions_list,
        "max_upload_mb": settings.max_upload_mb,
        "private_confirmation_ttl_seconds": settings.effective_private_confirmation_ttl_seconds,
        "authorized_senders_count": len(settings.authorized_senders_list),
        "sender_allowlist_enforced": _sender_allowlist_enforced(),
        "warnings": _setup_warnings(store_status, storage_backend),
    }


# --------------------------------------------------------------------------- login / dashboard


@app.get("/login")
def login_page(request: Request):
    if _is_dashboard_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return _login_html(error=None, status_code=200)


@app.post("/login")
def login_submit(request: Request, username: str = Form(""), password: str = Form("")):
    if not _dashboard_auth_enabled():
        return RedirectResponse(url="/", status_code=303)

    if not settings.dashboard_login_available:
        return _login_html(
            error="Sign-in is disabled because no dashboard password is configured. "
            "Set DASHBOARD_PASSWORD in the server environment, then restart the app.",
            status_code=503,
        )

    client_key = _client_ip(request)
    lockout_minutes = max(1, settings.login_lockout_minutes)
    client_locked = login_rate_limiter.is_locked(client_key, now=_now())
    account_locked = login_account_rate_limiter.is_locked(_LOGIN_ACCOUNT_KEY, now=_now())
    if client_locked or account_locked:
        logger.warning(
            "Blocked login attempt from %s (%s)", client_key, "client locked" if client_locked else "account locked"
        )
        return _login_html(
            error=f"Too many failed sign-in attempts. Please wait {lockout_minutes} minutes and try again.",
            status_code=429,
            username_hint=username.strip(),
            headers={"Retry-After": str(lockout_minutes * 60)},
        )

    # Evaluate both comparisons so timing does not reveal which one failed.
    username_ok = security.verify_password(username.strip(), settings.dashboard_username)
    password_ok = security.verify_password(password, settings.dashboard_password)
    if not (username_ok and password_ok):
        login_rate_limiter.register_failure(client_key, now=_now())
        login_account_rate_limiter.register_failure(_LOGIN_ACCOUNT_KEY, now=_now())
        logger.warning("Failed dashboard login from %s", client_key)
        request_logs.add({"request_id": _request_id(request), "type": "login", "status": "failed"})
        return _login_html(error="Incorrect username or password.", status_code=401, username_hint=username.strip())

    login_rate_limiter.reset(client_key)
    login_account_rate_limiter.reset(_LOGIN_ACCOUNT_KEY)
    token = security.create_session_token(_session_secret(), now=_now())
    request_logs.add({"request_id": _request_id(request), "type": "login", "status": "success"})

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=_DASHBOARD_SESSION_COOKIE,
        value=token,
        max_age=_session_ttl_seconds(),
        httponly=True,
        samesite="lax",
        secure=_session_cookie_secure(request),
        path="/",
    )
    return response


def _logout(request: Request) -> RedirectResponse:
    token = (request.cookies.get(_DASHBOARD_SESSION_COOKIE) or "").strip()
    if token:
        _revoke_session(token)

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(
        _DASHBOARD_SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_session_cookie_secure(request),
    )
    return response


@app.post("/logout")
def logout_post(request: Request):
    return _logout(request)


@app.get("/logout")
def logout(request: Request):
    return _logout(request)


@app.get("/")
def dashboard(request: Request):
    if not _is_dashboard_authenticated(request):
        return RedirectResponse(url="/login", status_code=303)
    auth_enabled = _dashboard_auth_enabled()
    page = build_dashboard_page(settings.dashboard_username if auth_enabled else None, auth_enabled)
    return HTMLResponse(content=page, headers=_HTML_SECURITY_HEADERS)


# --------------------------------------------------------------------------- documents


def _ensure_metadata_store_ready() -> None:
    ping = getattr(repository, "ping", None)
    if callable(ping):
        ping()


def _discard_stored_file(storage_path: str) -> None:
    """Best-effort cleanup of an uploaded file whose metadata could not be saved."""
    if is_remote_storage_path(storage_path):
        logger.warning("Uploaded remote object left without metadata (manual cleanup may be needed)")
        return
    try:
        Path(storage_path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove orphaned upload file", exc_info=True)


def _upload_file_name(raw_name: str | None) -> str:
    # Some browsers send a full client path; keep only the base name.
    return (raw_name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


@app.post("/upload", response_model=UploadResponse)
async def upload_document(
    request: Request,
    # Starlette turns a part with an empty filename into a plain string; accept it so the handler can
    # answer 400 "File name is required." instead of a 422 validation error.
    file: UploadFile | str | None = File(None),
    doc_category: str = Form(""),
    tags: str = Form(""),
    is_private: bool = Form(False),
) -> UploadResponse:
    _require_dashboard_auth(request)

    if not isinstance(file, StarletteUploadFile):
        raise HTTPException(status_code=400, detail="File name is required.")
    file_name = _upload_file_name(file.filename)
    if not file_name:
        raise HTTPException(status_code=400, detail="File name is required.")
    if len(file_name) > _MAX_FILE_NAME_LENGTH:
        raise HTTPException(status_code=400, detail=f"File name is too long (max {_MAX_FILE_NAME_LENGTH} characters).")

    extension = Path(file_name).suffix.lower().lstrip(".")
    if settings.allowed_extensions_list and extension not in settings.allowed_extensions_list:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{extension}")

    parsed_tags = [tag.strip().lower() for tag in tags.split(",") if tag.strip()]
    if len(parsed_tags) > _MAX_TAGS:
        raise HTTPException(status_code=400, detail=f"Too many tags (max {_MAX_TAGS}).")
    if any(len(tag) > _MAX_TAG_LENGTH for tag in parsed_tags):
        raise HTTPException(status_code=400, detail=f"Each tag must be at most {_MAX_TAG_LENGTH} characters.")

    input_category = doc_category.strip().lower()
    if len(input_category) > _MAX_CATEGORY_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Category must be at most {_MAX_CATEGORY_LENGTH} characters."
        )

    # Fail before storing the file when the metadata store is down (StoreUnavailableError -> 503).
    await run_in_threadpool(_ensure_metadata_store_ready)

    try:
        storage_path = await storage_service.save(file)
    except UploadRejectedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except StorageUnavailableError as exc:
        logger.error("Upload failed: file storage unavailable: %s", str(exc)[:200])
        raise HTTPException(
            status_code=503, detail="File storage is unavailable right now. Please try again later."
        ) from exc

    final_tags = tagging.derive_final_tags(parsed_tags, file_name, input_category or "general", extension)
    final_category, suggested_category, suggestion_confidence = tagging.choose_final_category(
        input_category, final_tags
    )

    document = DocumentMetadata(
        file_name=file_name,
        file_type=extension,
        doc_category=final_category,
        tags=final_tags,
        storage_path=storage_path,
        is_private=is_private,
    )

    try:
        await run_in_threadpool(repository.add, document)
    except Exception:
        logger.exception("Saving metadata failed; removing the stored file (doc_id=%s)", document.id)
        _discard_stored_file(storage_path)
        raise

    logger.info("Uploaded document: id=%s type=%s private=%s", document.id, extension, is_private)
    await _log_request_async(
        {
            "request_id": _request_id(request),
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

    return UploadResponse(message="Upload successful", document=DocumentPublic.from_metadata(document))


def _resolve_best_retrievable_document(
    query: str,
) -> tuple[RetrievalResult, int, list[DocumentMetadata]]:
    """Best match among active documents, skipping local records whose file is missing.

    Returns (result, skipped_missing_count, active_documents). Skipped documents are NOT archived:
    a missing file may be a deploy/cwd problem, and the owner decides what to do with it.
    """
    active_documents = repository.list_active()
    candidates = list(active_documents)
    skipped = 0

    while candidates:
        result = find_best_document(query, candidates)
        if not result.found or result.document is None:
            break
        document = result.document
        if is_remote_storage_path(document.storage_path):
            return result, skipped, active_documents
        if storage_service.resolve_local_path(document.storage_path) is not None:
            return result, skipped, active_documents

        skipped += 1
        logger.warning("Skipping document with a missing stored file during matching: doc_id=%s", document.id)
        candidates = [doc for doc in candidates if doc.id != document.id]

    return RetrievalResult(found=False, score=0.0), skipped, active_documents


@app.get("/get-document", response_model=PublicRetrievalResult)
def get_document(request: Request, query: str = "") -> PublicRetrievalResult:
    _require_dashboard_auth(request)

    result, _skipped, _active = _resolve_best_retrievable_document(query)
    safe_query = sanitize_text_for_logs(query)
    logger.info("Retrieval query processed: query=%s found=%s score=%.2f", safe_query, result.found, result.score)
    request_logs.add(
        {
            "request_id": _request_id(request),
            "type": "get-document",
            "query": safe_query,
            "found": result.found,
            "doc_id": result.document.id if result.document else None,
            "score": result.score,
        }
    )
    return PublicRetrievalResult.from_result(result)


@app.get("/documents", response_model=list[DocumentPublic])
def list_documents(request: Request, active_only: bool = True) -> list[DocumentPublic]:
    _require_dashboard_auth(request)
    documents = repository.list_active() if active_only else repository.list_all()
    return [DocumentPublic.from_metadata(document) for document in documents]


@app.delete("/documents/{document_id}")
def archive_document(document_id: str, request: Request) -> dict[str, str]:
    _require_dashboard_auth(request)

    archived = repository.deactivate(document_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Document not found")

    request_logs.add(
        {
            "request_id": _request_id(request),
            "type": "archive-document",
            "doc_id": document_id,
        }
    )
    return {"message": "Document archived"}


@app.head("/files/{document_id}", include_in_schema=False)
@app.get("/files/{document_id}")
def serve_document_file(
    document_id: str,
    request: Request,
    exp: str | None = None,
    sig: str | None = None,
):
    via_signature = bool(sig) and security.verify_file_access(document_id, exp, sig, get_app_secret(), now=_now())
    if not via_signature and not _is_dashboard_authenticated(request):
        raise HTTPException(status_code=403, detail="This link is invalid or has expired.")

    document = repository.get_by_id(document_id)
    if document is None or not document.is_active:
        raise HTTPException(status_code=404, detail="Document not found")

    media_type = guess_content_type(document.file_type or document.file_name)
    headers = {
        "Content-Disposition": _content_disposition(document.file_name),
        "Cache-Control": "private, no-store",
    }
    is_head = request.method == "HEAD"
    if not is_head:
        request_logs.add(
            {
                "request_id": _request_id(request),
                "type": "file-access",
                "doc_id": document.id,
                "via": "signed-link" if via_signature else "dashboard",
            }
        )

    if is_remote_storage_path(document.storage_path):
        if is_head:
            # Media fetchers may probe with HEAD first: answer from metadata without downloading the file.
            return Response(status_code=200, media_type=media_type, headers=headers)
        try:
            chunks = storage_service.iter_remote(document.storage_path)
        except StoredFileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Stored file missing") from exc
        except StorageUnavailableError as exc:
            logger.error("Remote file fetch failed for doc_id=%s: %s", document.id, str(exc)[:200])
            raise HTTPException(status_code=502, detail="File storage is unavailable right now.") from exc
        return StreamingResponse(chunks, media_type=media_type, headers=headers)

    file_path = storage_service.resolve_local_path(document.storage_path)
    if file_path is None:
        raise HTTPException(status_code=404, detail="Stored file missing")

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=document.file_name,
        content_disposition_type="inline",
        headers={"Cache-Control": headers["Cache-Control"]},
    )


# --------------------------------------------------------------------------- logs


def _validate_limit(limit: int, maximum: int) -> None:
    if limit < 1 or limit > maximum:
        raise HTTPException(status_code=400, detail=f"limit must be between 1 and {maximum}")


@app.get("/logs/recent")
def recent_logs(request: Request, limit: int = 20) -> list[dict]:
    _require_dashboard_auth(request)
    _validate_limit(limit, 200)
    return [_present_log_entry(entry) for entry in request_logs.latest(limit=limit)]


def _best_callback_by_sid(callback_logs: list[dict]) -> dict[str, dict]:
    """Per Twilio SID, the callback with the furthest delivery stage (ties: the later one)."""
    best_by_sid: dict[str, dict] = {}
    for entry in callback_logs:
        sid = str(entry.get("twilio_sid") or "").strip()
        if not sid:
            continue
        existing = best_by_sid.get(sid)
        if existing is None:
            best_by_sid[sid] = entry
            continue
        incoming_state = str(entry.get("normalized_state") or "unknown")
        existing_state = str(existing.get("normalized_state") or "unknown")
        if _delivery_stage_rank(incoming_state) >= _delivery_stage_rank(existing_state):
            best_by_sid[sid] = entry
    return best_by_sid


@app.get("/logs/delivery")
def recent_delivery_logs(request: Request, limit: int = 20) -> list[dict]:
    _require_dashboard_auth(request)
    _validate_limit(limit, 200)

    send_logs = request_logs.latest_by_type(log_type="webhook", limit=2000) + request_logs.latest_by_type(
        log_type="private-access-audit", limit=2000
    )
    send_logs.sort(key=lambda entry: str(entry.get("timestamp") or ""))
    best_callback = _best_callback_by_sid(
        request_logs.latest_by_type(log_type="twilio-status-callback", limit=4000)
    )

    correlated_logs: list[dict] = []
    for entry in reversed(send_logs):
        sid = str(entry.get("twilio_sid") or "").strip()
        if not sid:
            continue

        callback = best_callback.get(sid)
        correlated_logs.append(
            {
                "timestamp": entry.get("timestamp"),
                "request_id": entry.get("request_id"),
                "type": entry.get("type"),
                "sender": _log_sender(entry.get("sender")),
                "query": entry.get("query"),
                "doc_id": entry.get("doc_id"),
                "found": entry.get("found"),
                "status": entry.get("status"),
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
    _validate_limit(limit, 2000)

    latest_by_sid = _best_callback_by_sid(
        request_logs.latest_by_type(log_type="twilio-status-callback", limit=limit)
    )
    state_counter = Counter(str(entry.get("normalized_state") or "unknown") for entry in latest_by_sid.values())

    terminal_total = sum(count for state, count in state_counter.items() if state in _TERMINAL_DELIVERY_STATES)
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


# --------------------------------------------------------------------------- WhatsApp webhook


@dataclass
class _WebhookContext:
    request_id: str
    body: str
    sender: str  # normalized '+<digits>'
    message_sid: str
    base_url: str

    @property
    def safe_query(self) -> str:
        return sanitize_text_for_logs(self.body)

    @property
    def masked_sender(self) -> str:
        return mask_phone(self.sender)

    @property
    def status_callback(self) -> str:
        return f"{self.base_url}/webhook/status"


@dataclass
class _WebhookOutcome:
    message: str  # API/JSON message (also the user-facing text unless user_text is set)
    matched_document_id: str | None = None
    user_notified: bool = False  # the reply already reached the user through the Twilio REST API
    reply_to_user: bool = True  # False for ignored requests (unauthorized, duplicate)
    user_text: str | None = None
    media_url: str | None = None


def _twilio_request_is_valid(request: Request, form: Any) -> bool:
    if not settings.twilio_signature_required:
        return True
    candidate_urls = security.twilio_candidate_urls(
        str(request.url),
        settings.public_base_url,
        request.headers.get("x-forwarded-proto"),
        request.headers.get("x-forwarded-host"),
    )
    return security.is_valid_twilio_request(
        form,
        request.headers.get("X-Twilio-Signature", ""),
        [settings.twilio_auth_token, settings.twilio_secondary_auth_token],
        candidate_urls,
    )


def _send_reply(ctx: _WebhookContext, text: str) -> str | None:
    if not whatsapp_sender.enabled:
        return None
    return whatsapp_sender.send_text(to_number=ctx.sender, body=text, status_callback=ctx.status_callback)


def _reply(ctx: _WebhookContext, text: str, **outcome_fields: Any) -> tuple[_WebhookOutcome, str | None]:
    sid = _send_reply(ctx, text)
    return _WebhookOutcome(message=text, user_notified=bool(sid), **outcome_fields), sid


def _audit(ctx: _WebhookContext, doc_id: str | None, status: str, reason: str, twilio_sid: str | None = None) -> None:
    request_logs.add(
        {
            "request_id": ctx.request_id,
            "type": "private-access-audit",
            "sender": ctx.masked_sender,
            "doc_id": doc_id,
            "status": status,
            "reason": reason,
            "twilio_sid": twilio_sid,
            "message_sid": ctx.message_sid,
        }
    )


def _log_webhook(ctx: _WebhookContext, **fields: Any) -> None:
    entry = {
        "request_id": ctx.request_id,
        "type": "webhook",
        "sender": ctx.masked_sender,
        "query": ctx.safe_query,
        "found": False,
        "doc_id": None,
        "twilio_sid": None,
        "message_sid": ctx.message_sid,
    }
    entry.update(fields)
    request_logs.add(entry)


def _normalize_keyword(body: str) -> str:
    return (body or "").strip().lower().rstrip(".!?,;:")


def _classify_reply(body: str) -> tuple[bool, bool]:
    """(is_confirm, is_cancel) for a WhatsApp reply.

    Exact keywords ("yes", "1", "cancel!") count, and so do natural variants whose first word is a
    keyword and whose other words are only filler or emoji ("yes please", "Yes 👍", "ok send", "no thanks").
    """
    yes_keywords = settings.private_confirmation_yes_keywords_list
    cancel_keywords = settings.private_confirmation_cancel_keywords_list
    keyword = _normalize_keyword(body)
    if keyword in cancel_keywords:
        return False, True
    if keyword in yes_keywords:
        return True, False

    words = normalize_text(body).split()
    if not words:
        return False, False
    first, rest = words[0], words[1:]
    if not all(word in STOPWORDS for word in rest):
        return False, False
    if first in cancel_keywords and not any(word in yes_keywords for word in rest):
        return False, True
    if first in yes_keywords and not any(word in cancel_keywords for word in rest):
        return True, False
    return False, False


def _private_confirmation_prompt_text(file_name: str) -> str:
    yes_keywords = settings.private_confirmation_yes_keywords_list
    options = [f"'{yes_keywords[0].upper()}'"]
    if "1" in yes_keywords and yes_keywords[0] != "1":
        options.append("'1'")
    cancel_word = settings.private_confirmation_cancel_keywords_list[0].upper()
    ttl = settings.effective_private_confirmation_ttl_seconds
    return (
        f"🔒 {file_name} is a private document. "
        f"Reply {' or '.join(options)} within {ttl} seconds to confirm. "
        f"Reply '{cancel_word}' to stop."
    )


def _handle_pending_challenge(
    ctx: _WebhookContext,
    challenge: dict[str, Any],
    is_confirm: bool,
    is_cancel: bool,
) -> tuple[_WebhookOutcome | None, DocumentMetadata | None]:
    """Resolve a pending private challenge (already removed from the dict).

    Returns (outcome, None) when the request is fully answered, (None, document) when access was
    granted, and (None, None) when the body must be processed as a new query.
    """
    is_keyword = is_confirm or is_cancel
    doc_id = str(challenge.get("doc_id") or "")
    try:
        expires_at = float(challenge.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0

    if _now() > expires_at:
        if is_keyword:
            outcome, sid = _reply(ctx, "Private confirmation expired. Please request your private file again.")
            _audit(ctx, doc_id or None, "challenge-expired", "timeout", sid)
            return outcome, None
        _audit(ctx, doc_id or None, "challenge-expired", "timeout")
        return None, None

    document = repository.get_by_id(doc_id) if doc_id else None
    if document is None or not document.is_active or not document.is_private:
        if is_keyword:
            outcome, sid = _reply(ctx, "That private document is no longer available.")
            _audit(ctx, doc_id or None, "challenge-invalidated", "document-unavailable", sid)
            return outcome, None
        _audit(ctx, doc_id or None, "challenge-invalidated", "document-unavailable")
        return None, None

    if is_cancel:
        outcome, sid = _reply(ctx, "Private request canceled.")
        _audit(ctx, document.id, "challenge-canceled", "user-cancel", sid)
        return outcome, None

    if is_confirm:
        _audit(ctx, document.id, "access-granted", "yes-confirmation")
        return None, document

    if not normalized_tokens(ctx.body):
        # Nothing searchable ("thanks", "hmm", an emoji): keep waiting for the answer instead of
        # dropping the request for a search that cannot match.
        _restore_challenge(ctx.sender, challenge)
        outcome, sid = _reply(ctx, _private_confirmation_prompt_text(document.file_name))
        _audit(ctx, document.id, "challenge-reprompted", "no-searchable-words", sid)
        return outcome, None

    _audit(ctx, document.id, "challenge-superseded", "new-query")
    return None, None


def _restore_challenge(sender: str, challenge: dict[str, Any]) -> None:
    """Put a popped challenge back unless it expired or a newer one was issued meanwhile."""
    try:
        expires_at = float(challenge.get("expires_at") or 0)
    except (TypeError, ValueError):
        return
    if _now() <= expires_at:
        _private_access_challenges.setdefault(sender, challenge)


def _issue_private_challenge(ctx: _WebhookContext, document: DocumentMetadata) -> _WebhookOutcome:
    ttl = settings.effective_private_confirmation_ttl_seconds
    _private_access_challenges[ctx.sender] = {"doc_id": document.id, "expires_at": _now() + ttl}
    outcome, sid = _reply(ctx, _private_confirmation_prompt_text(document.file_name))
    _audit(ctx, document.id, "challenge-issued", "yes-confirmation-required", sid)
    logger.info("Private document challenge issued: sender=%s doc_id=%s", ctx.masked_sender, document.id)
    return outcome


def _deliver_document(ctx: _WebhookContext, document: DocumentMetadata, score: float | None) -> _WebhookOutcome:
    if not is_remote_storage_path(document.storage_path) and storage_service.resolve_local_path(document.storage_path) is None:
        # Never auto-archive: the owner decides (the file may be restorable).
        logger.warning("Matched document has no stored file: doc_id=%s", document.id)
        outcome, sid = _reply(
            ctx,
            f"I found {document.file_name}, but its stored file is no longer available. Please re-upload this document.",
        )
        _log_webhook(ctx, doc_id=document.id, twilio_sid=sid, error="stored-file-missing")
        return outcome

    media_url = security.build_signed_file_url(
        ctx.base_url, document.id, get_app_secret(), settings.file_url_ttl_seconds
    )
    sharing_text = f"Sharing: {document.file_name}"

    if not whatsapp_sender.enabled:
        _log_webhook(ctx, found=True, doc_id=document.id, score=score, delivery="sender-disabled")
        return _WebhookOutcome(
            message=f"Document found: {document.file_name}.",
            matched_document_id=document.id,
            user_text=sharing_text,
            media_url=media_url,
        )

    media_sid = whatsapp_sender.send_media(
        to_number=ctx.sender,
        body=sharing_text,
        media_url=media_url,
        status_callback=ctx.status_callback,
    )
    link_sid = None
    if media_sid:
        message = "Document found and sent to your WhatsApp."
        delivery = "media"
    else:
        minutes = max(1, settings.file_url_ttl_seconds // 60)
        link_sid = whatsapp_sender.send_text(
            to_number=ctx.sender,
            body=(
                f"I couldn't attach {document.file_name}. Open it here (link valid for {minutes} minutes): {media_url}"
            ),
            status_callback=ctx.status_callback,
        )
        message = "Document found but delivery failed. Please retry in a moment."
        delivery = "link" if link_sid else "failed"
        logger.warning(
            "Media delivery failed: sender=%s doc_id=%s error=%s link_sent=%s",
            ctx.masked_sender,
            document.id,
            whatsapp_sender.last_error,
            bool(link_sid),
        )

    logger.info("Webhook matched document: sender=%s doc_id=%s delivery=%s", ctx.masked_sender, document.id, delivery)
    _log_webhook(
        ctx,
        found=True,
        doc_id=document.id,
        score=score,
        twilio_sid=media_sid or link_sid,
        media_twilio_sid=media_sid,
        link_twilio_sid=link_sid,
        delivery=delivery,
        error=None if media_sid else whatsapp_sender.last_error,
    )
    return _WebhookOutcome(
        message=message,
        matched_document_id=document.id,
        user_notified=bool(media_sid or link_sid),
        user_text=sharing_text,
        media_url=media_url,
    )


def _no_match_hint(active_documents: list[DocumentMetadata]) -> str:
    words: list[str] = []
    for document in active_documents:
        if document.is_private:
            # Never reveal what private documents exist.
            continue
        candidates = [document.doc_category, *document.tags]
        for value in candidates:
            word = (value or "").strip().lower()
            if (
                not word
                or word in words
                or word in tagging.GENERIC_CATEGORIES
                or len(word) > 20
                or word.isdigit()
            ):
                continue
            words.append(word)
            if len(words) >= _NO_MATCH_HINT_WORDS:
                return ", ".join(words)
    return ", ".join(words)


def _handle_no_match(ctx: _WebhookContext, skipped: int, active_documents: list[DocumentMetadata]) -> _WebhookOutcome:
    if skipped > 0:
        text = (
            "I found older entries for this request, but those files are no longer available. "
            "Please re-upload that document."
        )
        error = "no-retrievable-document-missing-files"
    else:
        text = "Document not found. Please refine your request."
        hint = _no_match_hint(active_documents)
        if hint:
            text = f"{text} Try: {hint}."
        error = None
    outcome, sid = _reply(ctx, text)
    logger.info("Webhook no document match: sender=%s query=%s skipped=%s", ctx.masked_sender, ctx.safe_query, skipped)
    _log_webhook(ctx, twilio_sid=sid, error=error, skipped_missing_files=skipped)
    return outcome


def _handle_webhook_message(ctx: _WebhookContext) -> _WebhookOutcome:
    keyword = _normalize_keyword(ctx.body)
    is_confirm, is_cancel = _classify_reply(ctx.body)

    # Pop the pending challenge atomically, so two concurrent "yes" replies can never both be granted.
    challenge = _private_access_challenges.pop(ctx.sender, None)
    if challenge is not None:
        try:
            outcome, granted = _handle_pending_challenge(ctx, challenge, is_confirm, is_cancel)
        except StoreUnavailableError:
            # The user is told to try again shortly: keep their pending request for that retry.
            _restore_challenge(ctx.sender, challenge)
            raise
        if outcome is not None:
            return outcome
        if granted is not None:
            return _deliver_document(ctx, granted, score=None)
    elif is_confirm or is_cancel:
        outcome, sid = _reply(ctx, "No pending private confirmation. Please request a document first.")
        _audit(ctx, None, "no-active-challenge", "confirmation-without-challenge", sid)
        return outcome

    if not keyword:
        # Empty body (e.g. a media-only message): never run it through search.
        outcome, sid = _reply(ctx, "Please tell me which document you need, e.g. 'send my resume'.")
        _log_webhook(ctx, twilio_sid=sid, error="empty-query")
        return outcome

    result, skipped, active_documents = _resolve_best_retrievable_document(ctx.body)
    if not result.found or result.document is None:
        return _handle_no_match(ctx, skipped, active_documents)

    if result.document.is_private:
        return _issue_private_challenge(ctx, result.document)
    return _deliver_document(ctx, result.document, score=result.score)


def _process_webhook_message(ctx: _WebhookContext) -> _WebhookOutcome:
    """Runs in a worker thread (Twilio REST calls and store I/O block)."""
    try:
        return _handle_webhook_message(ctx)
    except StoreUnavailableError as exc:
        logger.error("Webhook could not reach the metadata store: %s", str(exc)[:200])
        outcome, sid = _reply(ctx, STORE_UNAVAILABLE_REPLY)
        _log_webhook(ctx, twilio_sid=sid, error="metadata-store-unavailable")
        return outcome


def _twiml(text: str | None = None, media_url: str | None = None) -> Response:
    if not text and not media_url:
        content = '<?xml version="1.0" encoding="UTF-8"?><Response/>'
    else:
        parts = []
        if text:
            parts.append(f"<Body>{xml_escape(text)}</Body>")
        if media_url:
            parts.append(f"<Media>{xml_escape(media_url)}</Media>")
        content = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{"".join(parts)}</Message></Response>'
    return Response(content=content, media_type="application/xml")


def _webhook_response(outcome: _WebhookOutcome, use_twiml: bool) -> Response:
    if not use_twiml:
        payload = WebhookResponse(message=outcome.message, matched_document_id=outcome.matched_document_id)
        return JSONResponse(content=payload.model_dump())
    # Real Twilio request: the reply normally goes out through the REST API, so answer with empty
    # TwiML. If it could not (sender disabled or the send failed), answer inline instead.
    if outcome.reply_to_user and not outcome.user_notified:
        return _twiml(outcome.user_text or outcome.message, outcome.media_url)
    return _twiml()


@app.post("/webhook", response_model=WebhookResponse)
async def whatsapp_webhook(request: Request) -> Response:
    form = await request.form()
    request_id = _request_id(request)
    body = str(form.get("Body") or "")
    raw_sender = str(form.get("From") or "")
    message_sid = str(form.get("MessageSid") or form.get("SmsMessageSid") or "").strip()
    use_twiml = "x-twilio-signature" in request.headers

    # 1. Signature first: unsigned/forged requests never touch the dedup set or the stores.
    if not _twilio_request_is_valid(request, form):
        logger.warning("Invalid Twilio signature blocked: request_id=%s path=%s", request_id, request.url.path)
        await _log_request_async({"request_id": request_id, "type": "webhook", "error": "invalid-twilio-signature"})
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    # 2. Sender.
    sender = normalize_phone(raw_sender)
    if not sender:
        await _log_request_async({"request_id": request_id, "type": "webhook", "error": "missing-sender"})
        raise HTTPException(status_code=400, detail="Missing sender")

    if not settings.is_sender_authorized(sender):
        # Never message numbers that are not on the allowlist.
        logger.warning("Unauthorized sender blocked: %s", mask_phone(sender))
        await _log_request_async(
            {
                "request_id": request_id,
                "type": "webhook",
                "sender": mask_phone(sender),
                "found": False,
                "error": "unauthorized-sender",
            }
        )
        return _webhook_response(_WebhookOutcome(message="Unauthorized sender.", reply_to_user=False), use_twiml)

    # 3. Dedup Twilio retries (only for verified, authorized requests).
    if message_sid and message_sid in _recent_message_sids_set:
        logger.info("Duplicate Twilio webhook ignored: sender=%s message_sid=%s", mask_phone(sender), message_sid)
        await _log_request_async(
            {
                "request_id": request_id,
                "type": "webhook",
                "sender": mask_phone(sender),
                "found": False,
                "error": "duplicate-message-sid",
                "message_sid": message_sid,
            }
        )
        return _webhook_response(
            _WebhookOutcome(message="Duplicate webhook ignored.", reply_to_user=False), use_twiml
        )

    if message_sid:
        _remember_message_sid(message_sid)

    ctx = _WebhookContext(
        request_id=request_id,
        body=body,
        sender=sender,
        message_sid=message_sid,
        base_url=_external_base_url(request),
    )
    logger.info("Webhook received: sender=%s query=%s", ctx.masked_sender, ctx.safe_query)
    try:
        outcome = await run_in_threadpool(_process_webhook_message, ctx)
    except Exception:
        # Let Twilio's retry of this message be processed instead of being dropped as a duplicate.
        _forget_message_sid(message_sid)
        raise
    return _webhook_response(outcome, use_twiml)


@app.post("/webhook/status")
async def twilio_status_callback(request: Request) -> dict[str, str]:
    form = await request.form()

    if not _twilio_request_is_valid(request, form):
        logger.warning("Invalid Twilio status callback signature blocked: request_id=%s", _request_id(request))
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    twilio_sid = str(form.get("MessageSid") or form.get("SmsSid") or "").strip()
    if not twilio_sid:
        return {"message": "Status callback ignored"}

    message_status = str(form.get("MessageStatus") or form.get("SmsStatus") or "").strip().lower()
    error_code = str(form.get("ErrorCode") or "").strip() or None
    error_message = str(form.get("ErrorMessage") or "").strip()[:300] or None
    normalized_state = _normalize_twilio_status(message_status=message_status, error_code=error_code)

    await _log_request_async(
        {
            "request_id": _request_id(request),
            "type": "twilio-status-callback",
            "twilio_sid": twilio_sid,
            "message_status": message_status,
            "normalized_state": normalized_state,
            "to": mask_phone(str(form.get("To") or "")),
            "from": mask_phone(str(form.get("From") or "")),
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
