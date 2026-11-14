from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from twilio.base.exceptions import TwilioRestException

from app.services import whatsapp as whatsapp_module
from app.services.whatsapp import (
    MAX_BODY_CHARS,
    WhatsAppSender,
    normalize_whatsapp_address,
    truncate_body,
)


def _sender(client: MagicMock, retries: int = 2, sleeps: list[float] | None = None) -> WhatsAppSender:
    recorded = sleeps if sleeps is not None else []
    return WhatsAppSender(
        "AC123",
        "token",
        "whatsapp:+14155238886",
        retries=retries,
        client=client,
        sleep=recorded.append,
    )


def _client(*side_effect) -> MagicMock:
    client = MagicMock()
    client.messages.create.side_effect = list(side_effect)
    return client


def _ok(sid: str = "SM123") -> SimpleNamespace:
    return SimpleNamespace(sid=sid)


def _rest_error(status: int, code: int | None = None) -> TwilioRestException:
    return TwilioRestException(status=status, uri="/Messages", msg="failure", code=code)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("whatsapp:+919876543210", "whatsapp:+919876543210"),
        ("WhatsApp: +91 98765-43210", "whatsapp:+919876543210"),
        ("+14155238886", "whatsapp:+14155238886"),
        ("919876543210", "whatsapp:+919876543210"),
        ("", ""),
        ("whatsapp:", ""),
    ],
)
def test_normalize_whatsapp_address(value: str, expected: str) -> None:
    assert normalize_whatsapp_address(value) == expected


def test_truncate_body() -> None:
    assert truncate_body("hi") == "hi"
    assert truncate_body("") == ""
    exact = "a" * MAX_BODY_CHARS
    assert truncate_body(exact) == exact
    long = truncate_body("b" * (MAX_BODY_CHARS + 50))
    assert len(long) == MAX_BODY_CHARS
    assert long.endswith("…")


def test_disabled_without_credentials(monkeypatch) -> None:
    client_cls = MagicMock()
    monkeypatch.setattr(whatsapp_module, "Client", client_cls)
    sender = WhatsAppSender("", "", "whatsapp:+14155238886")
    assert sender.enabled is False
    assert sender.send_text("whatsapp:+911234567890", "hi") is None
    client_cls.assert_not_called()

    assert WhatsAppSender("AC1", "tok", "").enabled is False


def test_client_constructor_failure_disables_sender(monkeypatch) -> None:
    monkeypatch.setattr(whatsapp_module, "Client", MagicMock(side_effect=RuntimeError("bad")))
    sender = WhatsAppSender("AC1", "tok", "whatsapp:+14155238886")
    assert sender.enabled is False
    assert sender.last_error is not None
    assert sender.send_text("+911234567890", "hi") is None


def test_uses_twilio_client_class_by_default(monkeypatch) -> None:
    client = MagicMock()
    client.messages.create.return_value = _ok("SMdefault")
    client_cls = MagicMock(return_value=client)
    monkeypatch.setattr(whatsapp_module, "Client", client_cls)

    sender = WhatsAppSender("AC1", "tok", "+14155238886")
    assert sender.enabled is True
    client_cls.assert_called_once()
    args, kwargs = client_cls.call_args
    assert args == ("AC1", "tok")
    # The REST client must have an HTTP timeout (the Twilio default is none).
    assert kwargs["http_client"].timeout == whatsapp_module.HTTP_TIMEOUT_SECONDS
    assert sender.send_text("+911234567890", "hi") == "SMdefault"


def test_send_text_success_normalizes_numbers_and_omits_status_callback() -> None:
    client = _client(_ok("SMtext"))
    sender = _sender(client)

    assert sender.send_text("+91 98765 43210", "hello") == "SMtext"
    client.messages.create.assert_called_once_with(
        from_="whatsapp:+14155238886",
        to="whatsapp:+919876543210",
        body="hello",
    )
    assert sender.last_error is None


def test_send_media_passes_media_url_and_status_callback() -> None:
    client = _client(_ok("SMmedia"))
    sender = _sender(client)

    sid = sender.send_media(
        "whatsapp:+919876543210",
        "Here is your file",
        "https://bot.example.com/files/doc1?exp=1&sig=abc",
        status_callback="https://bot.example.com/webhook/status",
    )
    assert sid == "SMmedia"
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["media_url"] == ["https://bot.example.com/files/doc1?exp=1&sig=abc"]
    assert kwargs["status_callback"] == "https://bot.example.com/webhook/status"
    assert kwargs["to"] == "whatsapp:+919876543210"


def test_send_text_with_status_callback() -> None:
    client = _client(_ok())
    _sender(client).send_text("+911234567890", "x", status_callback="https://cb.example.com/webhook/status")
    assert client.messages.create.call_args.kwargs["status_callback"] == "https://cb.example.com/webhook/status"
    assert "media_url" not in client.messages.create.call_args.kwargs


def test_body_is_truncated() -> None:
    client = _client(_ok())
    _sender(client).send_text("+911234567890", "z" * 5000)
    body = client.messages.create.call_args.kwargs["body"]
    assert len(body) == MAX_BODY_CHARS


@pytest.mark.parametrize("status,code", [(400, 21211), (400, 63016), (403, 20003), (404, None)])
def test_client_errors_are_not_retried(status: int, code: int | None) -> None:
    client = _client(_rest_error(status, code), _ok())
    sleeps: list[float] = []
    sender = _sender(client, sleeps=sleeps)

    assert sender.send_text("+911234567890", "hi") is None
    assert client.messages.create.call_count == 1
    assert sleeps == []
    assert sender.last_error is not None
    if code is not None:
        assert str(code) in sender.last_error


def test_server_error_is_retried_with_backoff() -> None:
    client = _client(_rest_error(500), _rest_error(503), _ok("SMlate"))
    sleeps: list[float] = []
    sender = _sender(client, retries=2, sleeps=sleeps)

    assert sender.send_text("+911234567890", "hi") == "SMlate"
    assert client.messages.create.call_count == 3
    assert sleeps == pytest.approx([0.4, 0.8])
    assert sender.last_error is None


def test_rate_limit_429_is_retried() -> None:
    client = _client(_rest_error(429, 20429), _ok("SM429"))
    assert _sender(client).send_text("+911234567890", "hi") == "SM429"
    assert client.messages.create.call_count == 2


def test_connection_error_retried_then_success() -> None:
    client = _client(requests.ConnectionError("reset"), _ok("SMconn"))
    sender = _sender(client)
    assert sender.send_media("+911234567890", "file", "https://x.example.com/f") == "SMconn"
    assert client.messages.create.call_count == 2


def test_retries_exhausted_returns_none() -> None:
    client = _client(_rest_error(500), _rest_error(500), _rest_error(500), _ok())
    sleeps: list[float] = []
    sender = _sender(client, retries=2, sleeps=sleeps)
    assert sender.send_text("+911234567890", "hi") is None
    assert client.messages.create.call_count == 3
    assert len(sleeps) == 2
    assert "500" in (sender.last_error or "")


def test_generic_exception_never_raises() -> None:
    client = MagicMock()
    client.messages.create.side_effect = ValueError("unexpected")
    sender = _sender(client, retries=1)
    assert sender.send_text("+911234567890", "hi") is None
    assert "ValueError" in (sender.last_error or "")


def test_zero_retries_means_single_attempt() -> None:
    client = _client(requests.Timeout("slow"), _ok())
    assert _sender(client, retries=0).send_text("+911234567890", "hi") is None
    assert client.messages.create.call_count == 1


def test_invalid_recipient_does_not_call_twilio() -> None:
    client = _client(_ok())
    sender = _sender(client)
    assert sender.send_text("", "hi") is None
    assert sender.send_text("whatsapp:", "hi") is None
    client.messages.create.assert_not_called()
    assert sender.last_error == "Invalid recipient number"


def test_logs_do_not_contain_body_or_full_number(caplog) -> None:
    client = _client(_rest_error(400, 21211))
    with caplog.at_level("WARNING", logger="instaretriv.whatsapp"):
        _sender(client).send_text("whatsapp:+919876543210", "secret body text")
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "secret body text" not in joined
    assert "9876543210" not in joined
