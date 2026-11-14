"""Regression tests for the defects found by the pre-hardening audit.

The first block ports the 15 audit tests that were marked ``xfail(strict=True)`` against the old
code; they are adapted to the hardened contract and must now PASS. The second block pins the
"low" audit findings (WhatsApp flow, logging, dashboard, fail-closed production behaviour).
Audit tests for behaviour the hardening spec intentionally changed (public /files redirects,
remote-URL probing and auto-archiving, the global private access code) were not ported.
"""

import logging
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import app.main as main_module
from app.config import Settings, settings
from app.request_log_repository import RequestLogRepository
from app.schemas import DocumentMetadata
from app.services.matcher import find_best_document
from app.services.whatsapp import WhatsAppSender
from app.stores import create_metadata_store

ALICE = "whatsapp:+15550001111"
BOB = "whatsapp:+15550002222"
AUTH_TOKEN = "test-twilio-token"
PASSWORD = "regression-pass-123"
WEBHOOK_URL = "http://testserver/webhook"


class RecordingSender:
    """WhatsAppSender stand-in that records every send and never touches the network."""

    def __init__(self, media_sid: str | None = "SMmedia", text_sid: str | None = "SMtext") -> None:
        self.enabled = True
        self.media_sid = media_sid
        self.text_sid = text_sid
        self.last_error: str | None = None
        self.text_calls: list[dict] = []
        self.media_calls: list[dict] = []

    def send_text(self, to_number: str, body: str, status_callback: str | None = None) -> str | None:
        self.text_calls.append({"to": to_number, "body": body, "status_callback": status_callback})
        return self.text_sid

    def send_media(self, to_number: str, body: str, media_url: str, status_callback: str | None = None) -> str | None:
        self.media_calls.append({"to": to_number, "body": body, "media_url": media_url})
        return self.media_sid


@pytest.fixture
def client() -> TestClient:
    return TestClient(main_module.app)


@pytest.fixture
def fake_sender(monkeypatch: pytest.MonkeyPatch) -> RecordingSender:
    sender = RecordingSender()
    monkeypatch.setattr(main_module, "whatsapp_sender", sender)
    return sender


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(now=1_800_000_000.0)
    monkeypatch.setattr(main_module, "_now", lambda: state.now)
    return state


@pytest.fixture
def blocked_http(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record (and refuse) any HTTP request made through `requests`."""
    calls: list[str] = []

    def refuse(self, method, url, *args, **kwargs):
        calls.append(f"{method} {url}")
        raise requests.ConnectionError("network blocked in regression tests")

    monkeypatch.setattr(requests.sessions.Session, "request", refuse)
    return calls


def _upload(client: TestClient, file_name: str, tags: str = "", category: str = "", private: bool = False,
            content: bytes = b"%PDF-1.4 regression", expect: int = 200):
    response = client.post(
        "/upload",
        data={"doc_category": category, "tags": tags, "is_private": "true" if private else "false"},
        files={"file": (file_name, content, "application/octet-stream")},
    )
    assert response.status_code == expect, response.text
    return response.json()["document"] if expect == 200 else response


def _webhook(client: TestClient, body: str, sender: str = ALICE, **extra) -> dict:
    response = client.post("/webhook", data={"From": sender, "Body": body, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _enable_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashboard_auth_enabled", True)
    monkeypatch.setattr(settings, "dashboard_username", "owner")
    monkeypatch.setattr(settings, "dashboard_password", PASSWORD)


def _require_signatures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "require_twilio_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)


def _sign(params: dict, url: str = WEBHOOK_URL, token: str = AUTH_TOKEN) -> str:
    return RequestValidator(token).compute_signature(url, params)


def _doc(tags: list[str], category: str = "general", file_name: str = "file.pdf") -> DocumentMetadata:
    return DocumentMetadata(
        file_name=file_name, file_type="pdf", doc_category=category, tags=tags, storage_path="unused.pdf"
    )


def _logs_of_type(log_type: str) -> list[dict]:
    return [entry for entry in main_module.request_logs.latest(limit=10_000) if entry.get("type") == log_type]


# =========================================================================== former xfail audit bugs


def test_get_document_requires_auth_when_enabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Was: anyone could query /get-document, read storage_path of PRIVATE docs and download them.
    _upload(client, "pan_card.pdf", tags="pan card", private=True)
    _enable_auth(monkeypatch)

    anonymous = TestClient(main_module.app)
    assert anonymous.get("/get-document", params={"query": "pan card"}).status_code == 401
    assert anonymous.get("/documents").status_code == 401


def test_local_doc_not_probed_or_archived_when_public_url_set(
    client: TestClient, fake_sender: RecordingSender, blocked_http: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Was: the webhook HTTP-probed its own /files URL and ARCHIVED the doc when the probe failed.
    monkeypatch.setattr(settings, "public_base_url", "https://pub.example")
    document = _upload(client, "resume.pdf", tags="resume")

    response = _webhook(client, "resume")

    assert blocked_http == []
    assert main_module.repository.get_by_id(document["id"]).is_active is True
    assert response["matched_document_id"] == document["id"]
    media_url = fake_sender.media_calls[-1]["media_url"]
    parts = urlsplit(media_url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"https://pub.example/files/{document['id']}"
    assert set(parse_qs(parts.query)) == {"exp", "sig"}


def test_delivery_logs_consistent_with_summary_for_out_of_order_callbacks(client: TestClient) -> None:
    # Was: /logs/delivery used the newest callback, so a late 'sent' regressed a 'delivered' message.
    main_module.request_logs.add({"type": "webhook", "twilio_sid": "SM1", "sender": ALICE, "found": True})
    for status in ("delivered", "sent"):
        response = client.post("/webhook/status", data={"MessageSid": "SM1", "MessageStatus": status})
        assert response.status_code == 200

    row = client.get("/logs/delivery").json()[0]
    summary = client.get("/logs/delivery/summary").json()
    assert summary["counts_by_state"] == {"delivered": 1}
    assert row["normalized_delivery_state"] == "delivered"


def test_synonym_tag_is_found_by_its_own_word() -> None:
    # Was: synonyms were applied to queries only, so a doc tagged 'cv' could not be found by 'cv'.
    assert find_best_document("send my cv", [_doc(["cv"])]).found is True


def test_filler_only_query_does_not_match() -> None:
    # Was: partial_ratio on 'me' scored 100 against 'resume'.
    resume = _doc(["resume"], category="resume")
    assert find_best_document("send me", [resume]).found is False


def test_archived_challenge_does_not_leak_other_private_doc(client: TestClient) -> None:
    # Was: after the challenged doc was archived, 'y' fell through to search and released ANOTHER
    # private document (fuzzy 'y' in 'identity') without any confirmation.
    pan = _upload(client, "pan_card.pdf", tags="pan card", private=True)
    other = _upload(client, "voter.pdf", category="general", tags="identity", private=True)
    _webhook(client, "pan card")
    assert main_module._private_access_challenges[main_module.normalize_phone(ALICE)]["doc_id"] == pan["id"]
    assert client.delete(f"/documents/{pan['id']}").status_code == 200

    response = _webhook(client, "y")

    assert response["matched_document_id"] != other["id"]
    assert response["matched_document_id"] is None
    assert response["message"] == "That private document is no longer available."


def test_private_doc_not_released_without_sender(client: TestClient) -> None:
    # Was: with no From, 'y' was searched and a private doc was returned with no challenge.
    _upload(client, "voter.pdf", category="general", tags="identity", private=True)
    response = client.post("/webhook", data={"Body": "y"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Missing sender"}


def test_request_logs_latest_non_positive_limit_returns_empty(tmp_path: Path) -> None:
    # Was: logs[-0:] returned everything.
    logs = RequestLogRepository(str(tmp_path / "r.json"))
    for index in range(5):
        logs.add({"type": "t", "n": index})
    assert logs.latest(limit=0) == []
    assert logs.latest(limit=-2) == []


def test_network_error_is_retried_and_does_not_raise() -> None:
    # Was: requests.ConnectionError from the Twilio client escaped and turned /webhook into a 500.
    twilio_client = MagicMock()
    twilio_client.messages.create.side_effect = [requests.exceptions.ConnectionError("reset"), SimpleNamespace(sid="SM5")]
    sleeps: list[float] = []
    sender = WhatsAppSender("ACfake", "fake_token", "+14155238886", retries=2, client=twilio_client, sleep=sleeps.append)

    assert sender.send_text("+15550001111", "x") == "SM5"
    assert len(sleeps) == 1


def test_upload_empty_file_rejected(client: TestClient) -> None:
    response = _upload(client, "empty.pdf", content=b"", expect=400)
    assert main_module.repository.list_all() == []
    assert "detail" in response.json()


def test_explicit_id_category_is_kept(client: TestClient) -> None:
    # Was: 'id' was treated as generic and silently replaced by a suggestion ('bank').
    assert _upload(client, "bank_statement.pdf", category="id")["doc_category"] == "id"


def test_files_archived_document_not_served(client: TestClient) -> None:
    from app import security

    document = _upload(client, "resume.pdf", tags="resume")
    exp, sig = security.sign_file_access(document["id"], security.get_app_secret(), 300)
    assert client.delete(f"/documents/{document['id']}").status_code == 200

    assert client.get(f"/files/{document['id']}").status_code == 404
    assert client.get(f"/files/{document['id']}", params={"exp": exp, "sig": sig}).status_code == 404


def test_dashboard_does_not_advertise_passcode_retrieval(client: TestClient) -> None:
    html = client.get("/").text.lower()
    assert "code 1234" not in html
    assert "passcode" not in html
    assert "access code" not in html


def test_rejected_request_does_not_poison_dedup(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Was: the MessageSid was remembered before the signature check, so a forged request with a
    # real SID made Twilio's genuine delivery look like a duplicate.
    _require_signatures(monkeypatch)
    params = {"From": ALICE, "Body": "resume", "MessageSid": "SMpoison"}

    forged = client.post("/webhook", data=params, headers={"X-Twilio-Signature": "forged"})
    assert forged.status_code == 403
    assert "SMpoison" not in main_module._recent_message_sids_set

    genuine = client.post("/webhook", data=params, headers={"X-Twilio-Signature": _sign(params)})
    assert genuine.status_code == 200
    assert "Duplicate" not in genuine.text


def test_application_log_does_not_contain_raw_secret(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="instaretriv")
    _webhook(client, "otp 482913")
    assert "482913" not in caplog.text
    assert "482913" not in str(_logs_of_type("webhook"))


# =========================================================================== other audit findings


def test_expired_confirmation_does_not_swallow_next_request(client: TestClient, clock: SimpleNamespace) -> None:
    _upload(client, "pan_card.pdf", tags="pan card", private=True)
    resume = _upload(client, "resume.pdf", tags="resume")
    _webhook(client, "pan card")
    clock.now += settings.effective_private_confirmation_ttl_seconds + 1

    response = _webhook(client, "resume")

    assert response["matched_document_id"] == resume["id"]
    assert main_module._private_access_challenges == {}


def test_confirmation_keyword_after_expiry_is_explained(client: TestClient, clock: SimpleNamespace) -> None:
    _upload(client, "pan_card.pdf", tags="pan card", private=True)
    _webhook(client, "pan card")
    clock.now += settings.effective_private_confirmation_ttl_seconds + 1

    response = _webhook(client, "yes")

    assert response["matched_document_id"] is None
    assert "expired" in response["message"].lower()


def test_prompt_uses_effective_ttl_and_configured_keywords(
    client: TestClient, fake_sender: RecordingSender, clock: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Was: the prompt said '5 seconds' while 15 were enforced, and always showed YES/1/CANCEL.
    monkeypatch.setattr(settings, "private_confirmation_ttl_seconds", 5)
    monkeypatch.setattr(settings, "private_confirmation_yes_keywords", "sure,1")
    monkeypatch.setattr(settings, "private_confirmation_cancel_keywords", "nope")
    _upload(client, "pan_card.pdf", tags="pan card", private=True)

    _webhook(client, "pan card")

    prompt = fake_sender.text_calls[-1]["body"]
    assert "within 15 seconds" in prompt
    assert "'SURE'" in prompt and "'1'" in prompt and "'NOPE'" in prompt
    challenge = main_module._private_access_challenges[main_module.normalize_phone(ALICE)]
    assert challenge["expires_at"] == clock.now + 15


def test_new_query_supersedes_pending_challenge(client: TestClient) -> None:
    _upload(client, "pan_card.pdf", tags="pan card", private=True)
    resume = _upload(client, "resume.pdf", tags="resume")
    _webhook(client, "pan card")

    response = _webhook(client, "resume")

    assert response["matched_document_id"] == resume["id"]
    assert main_module._private_access_challenges == {}
    assert _webhook(client, "yes")["matched_document_id"] is None


def test_challenges_are_per_sender(client: TestClient) -> None:
    pan = _upload(client, "pan_card.pdf", tags="pan card", private=True)
    _webhook(client, "pan card", sender=ALICE)

    bob = _webhook(client, "yes", sender=BOB)
    assert bob["matched_document_id"] is None
    assert "No pending private confirmation" in bob["message"]

    assert _webhook(client, "yes", sender=ALICE)["matched_document_id"] == pan["id"]


def test_unauthorized_sender_is_never_messaged_or_served(
    client: TestClient, fake_sender: RecordingSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upload(client, "resume.pdf", tags="resume")
    monkeypatch.setattr(settings, "authorized_senders", "whatsapp:+15550001111")

    response = _webhook(client, "resume", sender=BOB)

    assert response["matched_document_id"] is None
    assert fake_sender.text_calls == [] and fake_sender.media_calls == []


def test_signed_webhook_gets_twiml_with_inline_reply_when_sender_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Was: Twilio received JSON instead of TwiML.
    _require_signatures(monkeypatch)
    params = {"From": ALICE, "Body": "something unknown", "MessageSid": "SMtwiml1"}

    response = client.post("/webhook", data=params, headers={"X-Twilio-Signature": _sign(params)})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Response><Message>" in response.text and "Document not found" in response.text


def test_signed_webhook_gets_empty_twiml_when_reply_sent_via_rest(
    client: TestClient, fake_sender: RecordingSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_signatures(monkeypatch)
    params = {"From": ALICE, "Body": "something unknown", "MessageSid": "SMtwiml2"}

    response = client.post("/webhook", data=params, headers={"X-Twilio-Signature": _sign(params)})

    assert response.status_code == 200
    assert response.text.endswith("<Response/>")
    assert len(fake_sender.text_calls) == 1


def test_twilio_connection_errors_do_not_break_webhook_or_archive(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    twilio_client = MagicMock()
    twilio_client.messages.create.side_effect = requests.exceptions.ConnectionError("twilio unreachable")
    sender = WhatsAppSender("ACfake", "fake_token", "+14155238886", retries=1, client=twilio_client, sleep=lambda _s: None)
    monkeypatch.setattr(main_module, "whatsapp_sender", sender)
    monkeypatch.setattr(settings, "public_base_url", "https://pub.example")
    document = _upload(client, "resume.pdf", tags="resume")

    response = _webhook(client, "resume")

    assert response["message"] == "Document found but delivery failed. Please retry in a moment."
    assert main_module.repository.get_by_id(document["id"]).is_active is True


def test_request_log_sender_is_masked(client: TestClient) -> None:
    _webhook(client, "resume")
    entry = _logs_of_type("webhook")[-1]
    assert entry["sender"] != ALICE
    assert "5550001111" not in str(entry)


def test_session_cookie_is_secure_httponly_behind_https(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_auth(monkeypatch)
    response = client.post(
        "/login",
        data={"username": "owner", "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_setup_status_requires_auth_and_docs_off_in_production(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_auth(monkeypatch)
    assert TestClient(main_module.app).get("/setup/status").status_code == 401

    production = Settings(_env_file=None, app_env="production")
    assert main_module._api_docs_urls(production) == {"docs_url": None, "redoc_url": None, "openapi_url": None}


def test_no_default_dashboard_password(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Declared defaults (the test environment itself turns auth off).
    assert Settings.model_fields["dashboard_auth_enabled"].default is True
    assert Settings.model_fields["dashboard_password"].default == ""

    monkeypatch.setattr(settings, "dashboard_auth_enabled", True)
    monkeypatch.setattr(settings, "dashboard_password", "")
    response = client.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
    assert response.status_code == 503
    assert "set-cookie" not in response.headers


def test_failed_login_returns_html_not_json(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_auth(monkeypatch)
    response = client.post("/login", data={"username": "owner", "password": "wrong"}, follow_redirects=False)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path", ["/", "/login"])
def test_pages_have_mobile_viewport(client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    if path == "/login":
        _enable_auth(monkeypatch)
    html = client.get(path).text
    assert '<meta name="viewport"' in html


def test_production_mongo_without_uri_fails_closed(tmp_path: Path) -> None:
    # Was: Mongo silently fell back to an ephemeral JSON file, so production WhatsApp answered
    # "Document not found" for everything after the Atlas cluster disappeared.
    metadata_file = tmp_path / "metadata.json"
    production = Settings(
        _env_file=None, app_env="production", metadata_backend="mongo", mongodb_uri="", metadata_file=str(metadata_file)
    )

    store = create_metadata_store(production)
    status = store.status()

    assert status["available"] is False and status["fallback"] is False
    assert "MONGODB_URI" in (status["error"] or "")
    assert not metadata_file.exists()


def test_store_outage_gives_clear_whatsapp_reply_and_health_signals(
    client: TestClient, fake_sender: RecordingSender, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    production = Settings(
        _env_file=None, app_env="production", metadata_backend="mongo", mongodb_uri="",
        metadata_file=str(tmp_path / "metadata.json"),
    )
    monkeypatch.setattr(main_module, "repository", create_metadata_store(production))

    response = _webhook(client, "resume")

    assert response["message"] == main_module.STORE_UNAVAILABLE_REPLY
    assert "Document not found" not in fake_sender.text_calls[-1]["body"]
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["status"] == "degraded"
    assert client.get("/ready").status_code == 503
    upload = client.post("/upload", files={"file": ("resume.pdf", b"%PDF", "application/pdf")})
    assert upload.status_code == 503


def test_api_responses_never_expose_storage_paths(client: TestClient) -> None:
    document = _upload(client, "resume.pdf", tags="resume")
    assert "storage_path" not in document
    listed = client.get("/documents").json()
    assert all("storage_path" not in item for item in listed)
    found = client.get("/get-document", params={"query": "resume"}).json()
    assert found["found"] is True and "storage_path" not in found["document"]


def test_test_suite_cannot_reach_external_network(blocked_network_attempts: list[str]) -> None:
    # tests/conftest.py blocks non-loopback sockets and DNS for the whole session.
    before = len(blocked_network_attempts)
    try:
        with pytest.raises(OSError):
            socket.create_connection(("203.0.113.10", 443), timeout=1)
        with pytest.raises(OSError):
            socket.getaddrinfo("example.com", 443)
        assert len(blocked_network_attempts) == before + 2
    finally:
        # These attempts are intentional; don't let them fail the session.
        del blocked_network_attempts[before:]
