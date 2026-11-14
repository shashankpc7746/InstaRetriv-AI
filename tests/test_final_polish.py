"""Regression tests for the issues found in the final live QA pass."""

import logging

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from tests.test_webhook_flow import FakeSender, _send, _upload


@pytest.fixture
def client() -> TestClient:
    return TestClient(main_module.app)


@pytest.fixture
def fake_sender(monkeypatch: pytest.MonkeyPatch) -> FakeSender:
    sender = FakeSender()
    monkeypatch.setattr(main_module, "whatsapp_sender", sender)
    return sender


def test_no_match_hint_never_mentions_private_documents(client, fake_sender) -> None:
    _upload(client, "passport.pdf", "passport", category="id", private=True)
    _upload(client, "resume.pdf", "resume")

    reply = _send(client, "weather forecast")["message"]

    assert reply.startswith("Document not found.")
    assert "resume" in reply
    assert "passport" not in reply.lower()


def test_no_match_hint_is_omitted_when_only_private_documents_exist(client, fake_sender) -> None:
    _upload(client, "passport.pdf", "passport", private=True)

    reply = _send(client, "weather forecast")["message"]

    assert reply == "Document not found. Please refine your request."


def test_access_log_filter_strips_signed_link_and_query() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("1.2.3.4:5", "GET", "/files/abc?exp=123&sig=deadbeef", "1.1", 200),
        exc_info=None,
    )

    assert main_module._StripQueryStringFilter().filter(record) is True
    rendered = record.getMessage()
    assert "sig=" not in rendered and "deadbeef" not in rendered
    assert "/files/abc?[redacted]" in rendered


def test_access_log_filter_is_installed_once_on_uvicorn_access_logger() -> None:
    filters = [f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, main_module._StripQueryStringFilter)]
    assert len(filters) == 1


def test_access_log_filter_leaves_plain_paths_alone() -> None:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s %s %s", ("c", "GET", "/health"), None)
    main_module._StripQueryStringFilter().filter(record)
    assert record.args == ("c", "GET", "/health")


def test_short_error_keeps_first_clause_of_pymongo_error() -> None:
    long_error = (
        "127.0.0.1:1: [WinError 10061] No connection could be made because the target machine actively refused it "
        "(configured timeouts: socketTimeoutMS: 500.0ms, connectTimeoutMS: 500.0ms), Timeout: 0.5s, "
        "Topology Description: <TopologyDescription id: 1234, topology_type: Unknown, servers: [...]>"
    )
    short = main_module._short_error(long_error)
    assert "Topology Description" not in short and "Timeout: 0.5s" not in short
    assert len(short) <= 161
    assert main_module._short_error(None) is None


def test_production_http_public_base_url_is_warned(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "http://example.com")
    warnings = main_module._setup_warnings({"backend": "json", "available": True, "error": None, "fallback": False}, "local")
    assert any("https://" in w and "PUBLIC_BASE_URL" in w for w in warnings)

    monkeypatch.setattr(settings, "public_base_url", "https://example.com")
    warnings = main_module._setup_warnings({"backend": "json", "available": True, "error": None, "fallback": False}, "local")
    assert not any("does not start with https://" in w for w in warnings)
