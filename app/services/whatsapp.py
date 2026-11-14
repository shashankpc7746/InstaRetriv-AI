import logging
import time
from collections.abc import Callable
from typing import Any

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from app.config import normalize_phone
from app.security import mask_phone, mask_phone_numbers


logger = logging.getLogger("instaretriv.whatsapp")

# Twilio's hard limit for a WhatsApp message body.
MAX_BODY_CHARS = 1600
TRUNCATION_MARKER = "…"
# Per-request HTTP timeout for the Twilio REST API (the default client has none, so a stalled
# connection could hold a worker thread forever).
HTTP_TIMEOUT_SECONDS = 8.0
# Stop retrying once a send has taken this long, so a webhook answers well within Twilio's 15 s limit.
SEND_DEADLINE_SECONDS = 10.0


def normalize_whatsapp_address(number: str) -> str:
    """'+91 98765-43210' / 'whatsapp:+919876543210' -> 'whatsapp:+919876543210'; '' when no digits."""
    normalized = normalize_phone(number)
    return f"whatsapp:{normalized}" if normalized else ""


def truncate_body(body: str, limit: int = MAX_BODY_CHARS) -> str:
    text = body or ""
    if len(text) <= limit:
        return text
    return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def _is_permanent_failure(exc: Exception) -> bool:
    """Client errors (bad number, 63016 outside session window, ...) won't succeed on retry.

    429 (rate limited) is the exception: it is transient.
    """
    if not isinstance(exc, TwilioRestException):
        return False
    status = getattr(exc, "status", None)
    if not isinstance(status, int):
        return False
    return 400 <= status < 500 and status != 429


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, TwilioRestException):
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        message = (getattr(exc, "msg", "") or "").strip().splitlines()
        # Twilio messages can contain the recipient number (e.g. 21211): never keep it unmasked.
        first_line = mask_phone_numbers(message[0][:200]) if message else ""
        return f"TwilioRestException status={status} code={code}: {first_line}".rstrip(": ")
    return f"{type(exc).__name__}: {mask_phone_numbers(str(exc)[:200])}".rstrip(": ")


def _build_client(account_sid: str, auth_token: str) -> Any:
    from twilio.http.http_client import TwilioHttpClient

    return Client(account_sid, auth_token, http_client=TwilioHttpClient(timeout=HTTP_TIMEOUT_SECONDS))


class WhatsAppSender:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        sender: str,
        retries: int = 2,
        *,
        backoff_seconds: float = 0.4,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        deadline_seconds: float = SEND_DEADLINE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sender = normalize_whatsapp_address(sender)
        self._retries = max(0, int(retries))
        self._backoff_seconds = max(0.0, backoff_seconds)
        self._sleep = sleep
        self._deadline_seconds = max(0.0, deadline_seconds)
        self._clock = clock
        self.last_error: str | None = None
        self._client: Any | None = None

        if not (account_sid and auth_token and self._sender):
            return
        if client is not None:
            self._client = client
            return
        try:
            self._client = _build_client(account_sid, auth_token)
        except Exception as exc:  # never let sender construction break app start-up
            self.last_error = _describe_error(exc)
            logger.error("Could not initialise Twilio client: %s", self.last_error)
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def send_text(self, to_number: str, body: str, status_callback: str | None = None) -> str | None:
        return self._send_with_retry(to_number=to_number, body=body, status_callback=status_callback)

    def send_media(self, to_number: str, body: str, media_url: str, status_callback: str | None = None) -> str | None:
        return self._send_with_retry(
            to_number=to_number,
            body=body,
            media_url=media_url,
            status_callback=status_callback,
        )

    def _send_with_retry(
        self,
        to_number: str,
        body: str,
        media_url: str | None = None,
        status_callback: str | None = None,
    ) -> str | None:
        """Send a message; returns the Twilio SID or None. Never raises."""
        if self._client is None:
            return None

        normalized_to = normalize_whatsapp_address(to_number)
        if not normalized_to:
            self.last_error = "Invalid recipient number"
            logger.warning("Twilio send skipped: invalid recipient number")
            return None

        kwargs: dict[str, Any] = {
            "from_": self._sender,
            "to": normalized_to,
            "body": truncate_body(body),
        }
        if media_url:
            kwargs["media_url"] = [media_url]
        if status_callback:
            kwargs["status_callback"] = status_callback

        kind = "media" if media_url else "text"
        total_attempts = self._retries + 1
        started = self._clock()
        for attempt in range(1, total_attempts + 1):
            try:
                message = self._client.messages.create(**kwargs)
                sid = getattr(message, "sid", None)
                self.last_error = None
                return str(sid) if sid else None
            except Exception as exc:  # TwilioRestException, requests errors, anything else
                self.last_error = _describe_error(exc)
                permanent = _is_permanent_failure(exc)
                logger.warning(
                    "Twilio %s send to %s failed (attempt %s/%s%s): %s",
                    kind,
                    mask_phone(normalized_to),
                    attempt,
                    total_attempts,
                    ", permanent" if permanent else "",
                    self.last_error,
                )
                if permanent or attempt >= total_attempts:
                    break
                delay = self._backoff_seconds * (2 ** (attempt - 1))
                if self._clock() - started + delay >= self._deadline_seconds:
                    logger.warning("Twilio %s send: giving up after %.1fs (deadline)", kind, self._clock() - started)
                    break
                if delay > 0:
                    try:
                        self._sleep(delay)
                    except Exception:
                        pass

        return None
