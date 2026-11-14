"""WhatsApp webhook: private-confirmation state machine, signatures, dedup, delivery and response format."""

import logging
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import app.main as main_module
from app import security
from app.config import settings
from app.stores import ResilientMetadataStore, StoreUnavailableError

SENDER = "whatsapp:+12345678901"
AUTH_TOKEN = "test-twilio-token"


class FakeSender:
    """Stands in for WhatsAppSender; records every send."""

    def __init__(self, media_ok: bool = True, text_ok: bool = True) -> None:
        self.media_ok = media_ok
        self.text_ok = text_ok
        self.sent: list[dict] = []
        self.last_error: str | None = None
        self._counter = 0

    @property
    def enabled(self) -> bool:
        return True

    def _sid(self) -> str:
        self._counter += 1
        return f"SMFAKE{self._counter:04d}"

    def send_text(self, to_number: str, body: str, status_callback: str | None = None) -> str | None:
        self.sent.append({"kind": "text", "to": to_number, "body": body, "status_callback": status_callback})
        if not self.text_ok:
            self.last_error = "text failed"
            return None
        return self._sid()

    def send_media(self, to_number: str, body: str, media_url: str, status_callback: str | None = None) -> str | None:
        self.sent.append(
            {"kind": "media", "to": to_number, "body": body, "media_url": media_url, "status_callback": status_callback}
        )
        if not self.media_ok:
            self.last_error = "TwilioRestException status=400 code=63019: media failed"
            return None
        return self._sid()

    @property
    def media(self) -> list[dict]:
        return [item for item in self.sent if item["kind"] == "media"]

    @property
    def texts(self) -> list[dict]:
        return [item for item in self.sent if item["kind"] == "text"]


@pytest.fixture
def client() -> TestClient:
    return TestClient(main_module.app)


@pytest.fixture
def fake_sender(monkeypatch: pytest.MonkeyPatch) -> FakeSender:
    sender = FakeSender()
    monkeypatch.setattr(main_module, "whatsapp_sender", sender)
    return sender


def _upload(client: TestClient, file_name: str, tags: str, category: str = "", private: bool = False) -> str:
    response = client.post(
        "/upload",
        data={"doc_category": category, "tags": tags, "is_private": "true" if private else "false"},
        files={"file": (file_name, b"%PDF-1.4 test content", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["document"]["id"]


def _send(client: TestClient, body: str, sender: str = SENDER, **extra) -> dict:
    response = client.post("/webhook", data={"From": sender, "Body": body, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _sign(url: str, params: dict, token: str = AUTH_TOKEN) -> str:
    return RequestValidator(token).compute_signature(url, params)


def _require_signatures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "require_twilio_signature", True)
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)


def _audit_statuses() -> list[str]:
    return [
        entry.get("status")
        for entry in main_module.request_logs.latest(limit=200)
        if entry.get("type") == "private-access-audit"
    ]


# --------------------------------------------------------------------------- private challenge state machine


def test_challenge_prompt_uses_configured_keywords_and_effective_ttl(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "private_confirmation_ttl_seconds", 5)
    monkeypatch.setattr(settings, "private_confirmation_yes_keywords", "haan,1")
    monkeypatch.setattr(settings, "private_confirmation_cancel_keywords", "ruko,cancel")
    _upload(client, "pan_card.pdf", "pan", "id", private=True)

    message = _send(client, "send my pan card")["message"]

    # The prompt and the enforcement share one TTL (max(15, configured)).
    assert "within 15 seconds" in message
    assert "Reply 'HAAN' or '1'" in message
    assert "Reply 'RUKO' to stop" in message
    challenge = main_module._private_access_challenges["+12345678901"]
    assert 14 <= challenge["expires_at"] - time.time() <= 16


def test_confirm_with_punctuation_delivers_signed_link(client, fake_sender) -> None:
    doc_id = _upload(client, "pan_card.pdf", "pan", "id", private=True)

    _send(client, "send my pan card")
    payload = _send(client, "YES!")

    assert payload["matched_document_id"] == doc_id
    assert payload["message"] == "Document found and sent to your WhatsApp."
    assert len(fake_sender.media) == 1
    media = fake_sender.media[0]
    assert media["to"] == "+12345678901"
    assert media["status_callback"] == "http://testserver/webhook/status"
    parts = urlsplit(media["media_url"])
    assert parts.path == f"/files/{doc_id}"
    query = parse_qs(parts.query)
    assert security.verify_file_access(doc_id, query["exp"][0], query["sig"][0], security.get_app_secret())
    assert "access-granted" in _audit_statuses()
    assert "+12345678901" not in main_module._private_access_challenges


def test_cancel_drops_challenge(client, fake_sender) -> None:
    _upload(client, "pan_card.pdf", "pan", "id", private=True)
    _send(client, "send my pan card")

    payload = _send(client, "Cancel.")

    assert payload["message"] == "Private request canceled."
    assert main_module._private_access_challenges == {}
    assert fake_sender.media == []
    assert "challenge-canceled" in _audit_statuses()


def test_expired_challenge_with_keyword_replies_expired(client, fake_sender, monkeypatch) -> None:
    _upload(client, "pan_card.pdf", "pan", "id", private=True)
    _send(client, "send my pan card")

    monkeypatch.setattr(main_module, "_now", lambda: time.time() + 3600)
    payload = _send(client, "yes")

    assert payload["message"] == "Private confirmation expired. Please request your private file again."
    assert payload["matched_document_id"] is None
    assert fake_sender.media == []
    assert main_module._private_access_challenges == {}


def test_expired_challenge_does_not_swallow_a_new_query(client, fake_sender, monkeypatch) -> None:
    _upload(client, "pan_card.pdf", "pan", "id", private=True)
    resume_id = _upload(client, "resume.pdf", "resume,cv", "resume")
    _send(client, "send my pan card")

    monkeypatch.setattr(main_module, "_now", lambda: time.time() + 3600)
    payload = _send(client, "send my resume")

    assert payload["matched_document_id"] == resume_id
    assert "challenge-expired" in _audit_statuses()


def test_archived_challenged_doc_plus_y_never_delivers_another_private_doc(client, fake_sender) -> None:
    pan_id = _upload(client, "pan_card.pdf", "pan,card", "id", private=True)
    # A second private doc that a bare "y" could otherwise have matched.
    _upload(client, "y_report.pdf", "y,yes,ok", "report", private=True)
    _send(client, "send my pan card")
    assert client.delete(f"/documents/{pan_id}").status_code == 200

    payload = _send(client, "y")

    assert payload["message"] == "That private document is no longer available."
    assert payload["matched_document_id"] is None
    assert fake_sender.media == []
    assert main_module._private_access_challenges == {}


def test_challenged_doc_no_longer_private_plus_keyword(client, fake_sender) -> None:
    pan_id = _upload(client, "pan_card.pdf", "pan", "id", private=True)
    _send(client, "send my pan card")
    document = main_module.repository.get_by_id(pan_id)
    main_module.repository.add(document.model_copy(update={"is_private": False}))

    payload = _send(client, "1")

    assert payload["message"] == "That private document is no longer available."
    assert fake_sender.media == []


def test_archived_challenged_doc_plus_new_query_runs_search(client, fake_sender) -> None:
    pan_id = _upload(client, "pan_card.pdf", "pan", "id", private=True)
    resume_id = _upload(client, "resume.pdf", "resume", "resume")
    _send(client, "send my pan card")
    client.delete(f"/documents/{pan_id}")

    payload = _send(client, "send my resume")

    assert payload["matched_document_id"] == resume_id
    assert "challenge-invalidated" in _audit_statuses()


def test_new_private_query_supersedes_with_a_new_challenge(client, fake_sender) -> None:
    pan_id = _upload(client, "pan_card.pdf", "pan", "id", private=True)
    passport_id = _upload(client, "passport.pdf", "passport", "id", private=True)

    _send(client, "send my pan card")
    assert main_module._private_access_challenges["+12345678901"]["doc_id"] == pan_id
    message = _send(client, "passport please")["message"]

    assert "passport.pdf is a private document" in message
    assert main_module._private_access_challenges["+12345678901"]["doc_id"] == passport_id
    assert "challenge-superseded" in _audit_statuses()

    payload = _send(client, "yes")
    assert payload["matched_document_id"] == passport_id


def test_bare_keyword_without_challenge_never_runs_search(client, fake_sender) -> None:
    # A public document tagged with a keyword must not be returned for a bare "ok".
    _upload(client, "ok_form.pdf", "ok,form", "form")

    for keyword in ("ok", "YES", "1", "no", "cancel"):
        payload = _send(client, keyword)
        assert payload["message"] == "No pending private confirmation. Please request a document first."
        assert payload["matched_document_id"] is None
    assert fake_sender.media == []


def test_concurrent_confirmations_grant_once(client, fake_sender) -> None:
    doc_id = _upload(client, "pan_card.pdf", "pan", "id", private=True)
    _send(client, "send my pan card")

    first = _send(client, "yes")
    second = _send(client, "yes")

    assert first["matched_document_id"] == doc_id
    assert second["matched_document_id"] is None
    assert len(fake_sender.media) == 1


def test_challenge_is_keyed_by_normalized_sender(client, fake_sender) -> None:
    doc_id = _upload(client, "pan_card.pdf", "pan", "id", private=True)
    _send(client, "send my pan card", sender="whatsapp:+91 98765 43210")

    assert "+919876543210" in main_module._private_access_challenges
    payload = _send(client, "yes", sender="+919876543210")
    assert payload["matched_document_id"] == doc_id


# --------------------------------------------------------------------------- senders


def test_empty_from_is_rejected_even_with_keyword(client) -> None:
    _upload(client, "pan_card.pdf", "pan,yes", "id", private=True)

    for body in ("yes", "send my pan card"):
        response = client.post("/webhook", data={"Body": body})
        assert response.status_code == 400
        assert response.json()["detail"] == "Missing sender"
    response = client.post("/webhook", data={"From": "whatsapp:", "Body": "yes"})
    assert response.status_code == 400


def test_allowlist_matches_normalized_numbers(client, fake_sender, monkeypatch) -> None:
    monkeypatch.setattr(settings, "authorized_senders", "+919876543210")
    resume_id = _upload(client, "resume.pdf", "resume", "resume")

    payload = _send(client, "send my resume", sender="whatsapp:+91 98765 43210")

    assert payload["matched_document_id"] == resume_id


def test_unauthorized_sender_is_never_messaged(client, fake_sender, monkeypatch) -> None:
    monkeypatch.setattr(settings, "authorized_senders", "whatsapp:+19999999999")
    _upload(client, "resume.pdf", "resume", "resume")

    payload = _send(client, "send my resume")

    assert payload["message"] == "Unauthorized sender."
    assert fake_sender.sent == []


def test_production_empty_allowlist_fails_closed(client, fake_sender, monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "twilio_auth_token", AUTH_TOKEN)
    _upload(client, "resume.pdf", "resume", "resume")
    params = {"From": SENDER, "Body": "send my resume", "MessageSid": "SMPROD1"}

    response = client.post(
        "/webhook", data=params, headers={"X-Twilio-Signature": _sign("http://testserver/webhook", params)}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Message>" not in response.text
    assert fake_sender.sent == []

    # ALLOW_ANY_SENDER=true is the explicit opt-out.
    monkeypatch.setattr(settings, "allow_any_sender", True)
    params["MessageSid"] = "SMPROD2"
    response = client.post(
        "/webhook", data=params, headers={"X-Twilio-Signature": _sign("http://testserver/webhook", params)}
    )
    assert response.status_code == 200
    assert len(fake_sender.media) == 1


# --------------------------------------------------------------------------- signatures and dedup


def test_signature_always_required_in_production(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "require_twilio_signature", False)
    monkeypatch.setattr(settings, "allow_any_sender", True)

    response = client.post("/webhook", data={"From": SENDER, "Body": "hi"})

    assert response.status_code == 403


def test_dedup_happens_after_signature_check(client, fake_sender, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    resume_id = _upload(client, "resume.pdf", "resume", "resume")
    params = {"From": SENDER, "Body": "send my resume", "MessageSid": "SMDEDUP1"}

    unsigned = client.post("/webhook", data=params)
    forged = client.post("/webhook", data=params, headers={"X-Twilio-Signature": "forged"})
    assert unsigned.status_code == 403
    assert forged.status_code == 403
    assert "SMDEDUP1" not in main_module._recent_message_sids_set

    signature = _sign("http://testserver/webhook", params)
    valid = client.post("/webhook", data=params, headers={"X-Twilio-Signature": signature})
    assert valid.status_code == 200
    assert len(fake_sender.media) == 1
    assert "SMDEDUP1" in main_module._recent_message_sids_set

    retry = client.post("/webhook", data=params, headers={"X-Twilio-Signature": signature})
    assert retry.status_code == 200
    assert len(fake_sender.media) == 1  # the retry was ignored
    entries = main_module.request_logs.latest(limit=50)
    assert any(entry.get("error") == "duplicate-message-sid" for entry in entries)
    found = [entry for entry in entries if entry.get("found") is True]
    assert found and found[-1]["doc_id"] == resume_id
    # Rejected requests never store a message_sid (it would re-seed the dedup set after a restart).
    rejected = [entry for entry in entries if entry.get("error") == "invalid-twilio-signature"]
    assert len(rejected) == 2
    assert all("message_sid" not in entry for entry in rejected)


def test_message_sid_is_forgotten_when_processing_fails(fake_sender, monkeypatch) -> None:
    client = TestClient(main_module.app, raise_server_exceptions=False)
    resume_id = _upload(client, "resume.pdf", "resume", "resume")
    real_handler = main_module._handle_webhook_message

    def boom(ctx):
        raise RuntimeError("transient failure")

    monkeypatch.setattr(main_module, "_handle_webhook_message", boom)
    failed = client.post("/webhook", data={"From": SENDER, "Body": "send my resume", "MessageSid": "SMRETRY"})
    assert failed.status_code == 500
    assert "X-Request-ID" in failed.headers
    assert "SMRETRY" not in main_module._recent_message_sids_set
    assert "SMRETRY" not in main_module._recent_message_sids_queue

    monkeypatch.setattr(main_module, "_handle_webhook_message", real_handler)
    retried = client.post("/webhook", data={"From": SENDER, "Body": "send my resume", "MessageSid": "SMRETRY"})
    assert retried.status_code == 200
    assert retried.json()["matched_document_id"] == resume_id


def test_signature_validates_against_public_base_url(client, fake_sender, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    monkeypatch.setattr(settings, "public_base_url", "https://bot.example.com/")
    params = {"From": SENDER, "Body": "hello there", "MessageSid": "SMPUB1"}

    ok = client.post(
        "/webhook", data=params, headers={"X-Twilio-Signature": _sign("https://bot.example.com/webhook", params)}
    )
    assert ok.status_code == 200

    params["MessageSid"] = "SMPUB2"
    wrong = client.post(
        "/webhook", data=params, headers={"X-Twilio-Signature": _sign("https://other.example.com/webhook", params)}
    )
    assert wrong.status_code == 403


def test_signature_validates_behind_tls_proxy_headers(client, fake_sender, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    resume_id = _upload(client, "resume.pdf", "resume", "resume")
    params = {"From": SENDER, "Body": "send my resume", "MessageSid": "SMPROXY1"}
    headers = {
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "bot.onrender.com",
        "X-Twilio-Signature": _sign("https://bot.onrender.com/webhook", params),
    }

    response = client.post("/webhook", data=params, headers=headers)

    assert response.status_code == 200
    # The signed media link uses the proxy's public scheme/host.
    assert fake_sender.media[0]["media_url"].startswith(f"https://bot.onrender.com/files/{resume_id}?exp=")
    assert fake_sender.media[0]["status_callback"] == "https://bot.onrender.com/webhook/status"


def test_secondary_auth_token_is_accepted(client, fake_sender, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    monkeypatch.setattr(settings, "twilio_secondary_auth_token", "secondary-token")
    params = {"From": SENDER, "Body": "hello", "MessageSid": "SMSEC1"}

    response = client.post(
        "/webhook",
        data=params,
        headers={"X-Twilio-Signature": _sign("http://testserver/webhook", params, token="secondary-token")},
    )

    assert response.status_code == 200


# --------------------------------------------------------------------------- response format


def test_unsigned_request_gets_json(client) -> None:
    response = client.post("/webhook", data={"From": SENDER, "Body": "hello"})
    assert response.headers["content-type"].startswith("application/json")
    assert "message" in response.json()


def test_twilio_request_with_sender_enabled_gets_empty_twiml(client, fake_sender, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    _upload(client, "resume.pdf", "resume", "resume")
    params = {"From": SENDER, "Body": "send my resume", "MessageSid": "SMTWIML1"}

    response = client.post(
        "/webhook", data=params, headers={"X-Twilio-Signature": _sign("http://testserver/webhook", params)}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert response.text.endswith("<Response/>")
    assert len(fake_sender.media) == 1


def test_twilio_request_with_sender_disabled_gets_inline_twiml_reply(client, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    resume_id = _upload(client, "résumé & cv.pdf", "resume", "resume")
    params = {"From": SENDER, "Body": "send my resume", "MessageSid": "SMTWIML2"}

    response = client.post(
        "/webhook", data=params, headers={"X-Twilio-Signature": _sign("http://testserver/webhook", params)}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Message><Body>Sharing: résumé &amp; cv.pdf</Body>" in response.text
    assert f"<Media>http://testserver/files/{resume_id}?exp=" in response.text
    assert "&amp;sig=" in response.text


def test_twilio_request_no_match_with_sender_disabled_replies_inline(client, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    params = {"From": SENDER, "Body": "send my passport", "MessageSid": "SMTWIML3"}

    response = client.post(
        "/webhook", data=params, headers={"X-Twilio-Signature": _sign("http://testserver/webhook", params)}
    )

    assert "<Message><Body>Document not found. Please refine your request.</Body></Message>" in response.text


def test_twilio_request_with_failed_rest_send_falls_back_to_inline_twiml(client, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    monkeypatch.setattr(main_module, "whatsapp_sender", FakeSender(text_ok=False))
    params = {"From": SENDER, "Body": "yes", "MessageSid": "SMTWIML4"}

    response = client.post(
        "/webhook", data=params, headers={"X-Twilio-Signature": _sign("http://testserver/webhook", params)}
    )

    assert "No pending private confirmation" in response.text


# --------------------------------------------------------------------------- delivery and matching


def test_store_unavailable_gets_friendly_reply(client, fake_sender, monkeypatch) -> None:
    def broken_factory():
        raise StoreUnavailableError("cluster deleted")

    monkeypatch.setattr(main_module, "repository", ResilientMetadataStore(broken_factory, backend_name="mongo"))

    payload = _send(client, "send my resume", MessageSid="SMSTORE1")

    assert "can't reach its document database" in payload["message"]
    assert fake_sender.texts and "can't reach its document database" in fake_sender.texts[0]["body"]
    entries = main_module.request_logs.latest(limit=10)
    assert entries[-1]["error"] == "metadata-store-unavailable"


def test_media_failure_sends_signed_link_and_records_both_sids(client, monkeypatch) -> None:
    sender = FakeSender(media_ok=False)
    monkeypatch.setattr(main_module, "whatsapp_sender", sender)
    resume_id = _upload(client, "resume.pdf", "resume", "resume")

    payload = _send(client, "send my resume")

    assert payload["message"] == "Document found but delivery failed. Please retry in a moment."
    assert payload["matched_document_id"] == resume_id
    assert len(sender.texts) == 1
    assert f"/files/{resume_id}?exp=" in sender.texts[0]["body"]
    entry = [e for e in main_module.request_logs.latest(limit=10) if e.get("type") == "webhook"][-1]
    assert entry["delivery"] == "link"
    assert entry["twilio_sid"] == entry["link_twilio_sid"]
    assert entry["media_twilio_sid"] is None


def test_missing_local_file_is_skipped_not_archived(client, fake_sender) -> None:
    doc_id = _upload(client, "resume.pdf", "resume", "resume")
    stored = main_module.repository.get_by_id(doc_id)
    Path(stored.storage_path).unlink()

    payload = _send(client, "send my resume")

    assert "no longer available" in payload["message"]
    assert payload["matched_document_id"] is None
    assert main_module.repository.get_by_id(doc_id).is_active is True


def test_no_match_reply_includes_hint_words(client, fake_sender) -> None:
    _upload(client, "resume.pdf", "resume,cv", "resume")
    _upload(client, "passport.pdf", "passport", "id")

    payload = _send(client, "send my driving licence")

    assert payload["message"].startswith("Document not found. Please refine your request. Try: ")
    assert "passport" in payload["message"] and "resume" in payload["message"]


def test_empty_body_never_searches(client, fake_sender) -> None:
    _upload(client, "resume.pdf", "resume", "resume")
    payload = _send(client, "   ")
    assert payload["matched_document_id"] is None


def test_logs_never_contain_raw_body_or_full_number(client, fake_sender, caplog) -> None:
    _upload(client, "pan_card.pdf", "pan", "id", private=True)
    caplog.set_level(logging.INFO)

    _send(client, "send my pan card code 98765")

    stored = str(main_module.request_logs.latest(limit=50))
    assert "98765" not in stored
    assert "12345678901" not in stored
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "98765" not in log_text
    assert "12345678901" not in log_text


# --------------------------------------------------------------------------- status callbacks and delivery logs


def test_status_callback_requires_signature_when_enabled(client, monkeypatch) -> None:
    _require_signatures(monkeypatch)
    response = client.post("/webhook/status", data={"MessageSid": "SM1", "MessageStatus": "delivered"})
    assert response.status_code == 403


def test_status_callback_without_sid_is_ignored(client) -> None:
    response = client.post("/webhook/status", data={"MessageStatus": "delivered"})
    assert response.status_code == 200
    assert main_module.request_logs.latest_by_type("twilio-status-callback", limit=10) == []


def test_delivery_logs_use_furthest_stage_and_include_private_audit(client) -> None:
    logs = main_module.request_logs
    logs.add({"type": "private-access-audit", "sender": SENDER, "doc_id": "d1", "status": "challenge-issued", "twilio_sid": "SMA"})
    logs.add({"type": "twilio-status-callback", "twilio_sid": "SMA", "message_status": "read", "normalized_state": "read"})
    # A late, lower-stage callback must not win.
    logs.add({"type": "twilio-status-callback", "twilio_sid": "SMA", "message_status": "sent", "normalized_state": "sent"})

    payload = client.get("/logs/delivery", params={"limit": 10}).json()

    assert len(payload) == 1
    assert payload[0]["twilio_sid"] == "SMA"
    assert payload[0]["normalized_delivery_state"] == "read"
    assert payload[0]["type"] == "private-access-audit"
    assert payload[0]["sender"] == "+12*****8901"
