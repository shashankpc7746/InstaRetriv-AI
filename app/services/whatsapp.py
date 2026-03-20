from twilio.rest import Client


class WhatsAppSender:
    def __init__(self, account_sid: str, auth_token: str, sender: str) -> None:
        self._enabled = bool(account_sid and auth_token and sender)
        self._sender = sender
        self._client = Client(account_sid, auth_token) if self._enabled else None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send_text(self, to_number: str, body: str) -> str | None:
        if not self._enabled or self._client is None:
            return None

        message = self._client.messages.create(
            from_=self._sender,
            to=to_number,
            body=body,
        )
        return message.sid

    def send_media(self, to_number: str, body: str, media_url: str) -> str | None:
        if not self._enabled or self._client is None:
            return None

        message = self._client.messages.create(
            from_=self._sender,
            to=to_number,
            body=body,
            media_url=[media_url],
        )
        return message.sid
