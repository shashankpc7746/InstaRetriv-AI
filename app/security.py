"""Security primitives: secrets, sessions, signed file links, Twilio validation, log redaction.

Everything here is a small pure function (or a tiny thread-safe class) so it can be unit tested
without importing the FastAPI app.
"""

import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from urllib.parse import quote, urlsplit, urlunsplit

from twilio.request_validator import RequestValidator

from app.config import normalize_phone, settings

logger = logging.getLogger("instaretriv.security")

# Allowed clock skew for tokens that appear to be issued in the future.
MAX_CLOCK_SKEW_SECONDS = 60

_SESSION_DOMAIN = "instaretriv.session.v1"
_FILE_DOMAIN = "instaretriv.file.v1"
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_generated_secret: str | None = None
_secret_lock = threading.Lock()


# --------------------------------------------------------------------------- secrets / passwords


def get_app_secret() -> str:
    """Return APP_SECRET_KEY, or a random per-process secret when it is not configured."""
    configured = (settings.app_secret_key or "").strip()
    if configured:
        return configured

    global _generated_secret
    if _generated_secret is None:
        with _secret_lock:
            if _generated_secret is None:
                _generated_secret = secrets.token_urlsafe(32)
                message = (
                    "APP_SECRET_KEY is not set: using a random per-process secret. Dashboard sessions and "
                    "signed file links will stop working after every restart."
                )
                if settings.is_production:
                    logger.error("%s Set APP_SECRET_KEY in production.", message)
                else:
                    logger.warning(message)
    return _generated_secret


def verify_password(provided: str, expected: str) -> bool:
    """Constant-time comparison. An empty expected value never matches (login disabled)."""
    if not expected:
        return False
    return hmac.compare_digest((provided or "").encode("utf-8"), expected.encode("utf-8"))


def _hmac_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _now(now: float | None) -> float:
    return time.time() if now is None else now


# --------------------------------------------------------------------------- login rate limiting


class LoginRateLimiter:
    """In-memory per-key (client IP) failed-login limiter.

    A key is locked for ``lockout_seconds`` once it reaches ``max_attempts`` failures within a
    sliding window of ``lockout_seconds``.
    """

    _MAX_TRACKED_KEYS = 10_000

    def __init__(self, max_attempts: int, lockout_seconds: int) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_locked(self, key: str, now: float | None = None) -> bool:
        current = _now(now)
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return False
            if current < until:
                return True
            del self._locked_until[key]
            return False

    def register_failure(self, key: str, now: float | None = None) -> None:
        current = _now(now)
        window_start = current - self.lockout_seconds
        with self._lock:
            if len(self._failures) >= self._MAX_TRACKED_KEYS:
                self._prune(current)
            recent = [stamp for stamp in self._failures.get(key, []) if stamp > window_start]
            recent.append(current)
            if len(recent) >= self.max_attempts:
                self._locked_until[key] = current + self.lockout_seconds
                self._failures.pop(key, None)
                logger.warning("Login locked for %s after %s failed attempts", key, len(recent))
            else:
                self._failures[key] = recent

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()

    def _prune(self, current: float) -> None:
        window_start = current - self.lockout_seconds
        for key in list(self._failures):
            if not any(stamp > window_start for stamp in self._failures[key]):
                del self._failures[key]
        for key in list(self._locked_until):
            if self._locked_until[key] <= current:
                del self._locked_until[key]


# --------------------------------------------------------------------------- dashboard sessions


def create_session_token(secret: str, now: float | None = None) -> str:
    """Stateless session token: '<issued_at>.<nonce>.<hmac_sha256_hex>'."""
    issued_at = int(_now(now))
    nonce = secrets.token_urlsafe(16)
    signature = _hmac_hex(secret, f"{_SESSION_DOMAIN}|{issued_at}|{nonce}")
    return f"{issued_at}.{nonce}.{signature}"


def validate_session_token(token: str, secret: str, ttl_seconds: int, now: float | None = None) -> bool:
    if not token or not secret or ttl_seconds <= 0 or len(token) > 256:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    issued_raw, nonce, signature = parts
    if not issued_raw.isdigit() or not _NONCE.match(nonce) or not _HEX_SHA256.match(signature):
        return False

    expected = _hmac_hex(secret, f"{_SESSION_DOMAIN}|{issued_raw}|{nonce}")
    if not hmac.compare_digest(expected, signature):
        return False

    issued_at = int(issued_raw)
    current = _now(now)
    if issued_at - current > MAX_CLOCK_SKEW_SECONDS:
        return False
    return current - issued_at <= ttl_seconds


# --------------------------------------------------------------------------- signed file links


def _file_signature(document_id: str, exp: int, secret: str) -> str:
    return _hmac_hex(secret, f"{_FILE_DOMAIN}|{document_id}|{exp}")


def sign_file_access(document_id: str, secret: str, ttl_seconds: int, now: float | None = None) -> tuple[int, str]:
    exp = int(_now(now)) + max(1, int(ttl_seconds))
    return exp, _file_signature(document_id, exp, secret)


def verify_file_access(
    document_id: str,
    exp: str | int | None,
    sig: str | None,
    secret: str,
    now: float | None = None,
) -> bool:
    if not document_id or not secret or not sig or exp is None:
        return False
    if isinstance(exp, bool):
        return False
    if isinstance(exp, int):
        exp_value = exp
    else:
        exp_text = str(exp).strip()
        if not exp_text.isdigit() or len(exp_text) > 12:
            return False
        exp_value = int(exp_text)

    signature = sig.strip().lower()
    if not _HEX_SHA256.match(signature):
        return False
    if not hmac.compare_digest(_file_signature(document_id, exp_value, secret), signature):
        return False
    return _now(now) <= exp_value


def build_signed_file_url(base_url: str, document_id: str, secret: str, ttl_seconds: int) -> str:
    exp, sig = sign_file_access(document_id, secret, ttl_seconds)
    return f"{base_url.rstrip('/')}/files/{quote(document_id, safe='')}?exp={exp}&sig={sig}"


# --------------------------------------------------------------------------- Twilio webhook signatures


def _first_header_value(value: str | None) -> str:
    """Proxies may chain values ('https,http'); the first hop is the client-facing one."""
    if not value:
        return ""
    return value.split(",", 1)[0].strip()


def twilio_candidate_urls(
    request_url: str,
    public_base_url: str,
    forwarded_proto: str | None,
    forwarded_host: str | None,
) -> list[str]:
    """Ordered, de-duplicated URLs that Twilio may have signed for this request."""
    candidates: list[str] = []

    def add(url: str) -> None:
        if url and url not in candidates:
            candidates.append(url)

    parsed = urlsplit(request_url)
    path_and_query = parsed.path or "/"
    if parsed.query:
        path_and_query = f"{path_and_query}?{parsed.query}"

    base = (public_base_url or "").strip()
    if base:
        add(f"{base.rstrip('/')}{path_and_query}")

    proto = _first_header_value(forwarded_proto).lower()
    host = _first_header_value(forwarded_host)
    if proto or host:
        add(
            urlunsplit(
                (
                    proto or parsed.scheme,
                    host or parsed.netloc,
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
        )

    add(request_url)
    return candidates


def is_valid_twilio_request(
    form_data: dict[str, str],
    signature: str,
    auth_tokens: list[str],
    candidate_urls: list[str],
) -> bool:
    """True when the signature matches any (auth token, URL) combination."""
    if not signature or not signature.strip():
        return False
    tokens = [token.strip() for token in auth_tokens if token and token.strip()]
    urls = [url for url in candidate_urls if url]
    for token in tokens:
        validator = RequestValidator(token)
        for url in urls:
            try:
                if validator.validate(url, form_data, signature.strip()):
                    return True
            except Exception:  # noqa: BLE001 - malformed input must never crash the webhook
                logger.debug("Twilio signature validation raised for a candidate URL", exc_info=True)
    return False


# --------------------------------------------------------------------------- log redaction


def mask_phone(value: str) -> str:
    """'whatsapp:+919876543210' -> '+91******3210'. Never returns the full number."""
    if not value or not str(value).strip():
        return ""
    digits = normalize_phone(str(value)).lstrip("+")
    if not digits:
        return "***"
    if len(digits) <= 4:
        return "+" + "*" * len(digits)
    if len(digits) <= 6:
        return "+" + "*" * (len(digits) - 2) + digits[-2:]
    return "+" + digits[:2] + "*" * (len(digits) - 6) + digits[-4:]


_SECRET_PHRASE = re.compile(r"\b(code|pin|otp|passcode|password)\b(\s*[:=#-]?\s*)\S+", re.IGNORECASE)
_PHONE_LIKE = re.compile(r"(?<![\w*])\+?\d{11,15}(?!\w)")
_BARE_DIGITS = re.compile(r"(?<![\w*])\d{4,10}(?!\w)")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


def mask_phone_numbers(text: str) -> str:
    """Mask every phone-number-like run (11-15 digits, optional '+') in free text, e.g. provider errors."""
    if not text:
        return ""
    return _PHONE_LIKE.sub(lambda m: mask_phone(m.group(0)), str(text))


def sanitize_text_for_logs(text: str, max_len: int = 120) -> str:
    """Make user text safe to log: redact secrets/numbers, single line, bounded length."""
    if not text:
        return ""
    cleaned = _CONTROL_CHARS.sub(" ", str(text))
    cleaned = _SECRET_PHRASE.sub(lambda m: f"{m.group(1)} [redacted]", cleaned)
    cleaned = _PHONE_LIKE.sub(lambda m: mask_phone(m.group(0)), cleaned)
    cleaned = _BARE_DIGITS.sub("****", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if max_len <= 0:
        return ""
    if len(cleaned) > max_len:
        cleaned = cleaned[: max(0, max_len - 1)].rstrip() + "…"
    return cleaned
