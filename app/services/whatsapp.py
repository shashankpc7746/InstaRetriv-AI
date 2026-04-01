import logging
import time

from twilio.base.exceptions import TwilioException
from twilio.rest import Client


logger = logging.getLogger("instaretriv.whatsapp")


class WhatsAppSender:
    def __init__(self, account_sid: str, auth_token: str, sender: str, retries: int = 2) -> None:
        self._enabled = bool(account_sid and auth_token and sender)
        self._sender = self._normalize_whatsapp_number(sender)
        self._retries = max(0, retries)
        self._client = Client(account_sid, auth_token) if self._enabled else None

    @staticmethod
    def _normalize_whatsapp_number(number: str) -> str:
        value = (number or "").strip()
        if not value:
            return value
        if value.lower().startswith("whatsapp:"):
            return f"whatsapp:{value.split(':', 1)[1].strip()}"
        return f"whatsapp:{value}"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send_text(self, to_number: str, body: str) -> str | None:
        return self._send_with_retry(to_number=to_number, body=body)

    def send_media(self, to_number: str, body: str, media_url: str) -> str | None:
        return self._send_with_retry(to_number=to_number, body=body, media_url=media_url)

    def _send_with_retry(self, to_number: str, body: str, media_url: str | None = None) -> str | None:
        if not self._enabled or self._client is None:
            return None

        normalized_to = self._normalize_whatsapp_number(to_number)

        total_attempts = self._retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                kwargs = {
                    "from_": self._sender,
                    "to": normalized_to,
                    "body": body,
                }
                if media_url:
                    kwargs["media_url"] = [media_url]

                message = self._client.messages.create(**kwargs)
                return message.sid
            except TwilioException as exc:
                logger.warning(
                    "Twilio send failed (attempt %s/%s): %s",
                    attempt,
                    total_attempts,
                    str(exc),
                )
                if attempt < total_attempts:
                    time.sleep(0.4)

        return None
