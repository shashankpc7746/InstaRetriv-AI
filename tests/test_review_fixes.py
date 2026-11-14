"""Regression tests for the second review round (security, WhatsApp flow, matching, stores)."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from twilio.base.exceptions import TwilioRestException

import app.main as main_module
from app import security
from app.config import settings
from app.repository import MetadataRepository
from app.request_log_repository import RequestLogRepository
from app.schemas import DocumentMetadata
from app.services import tagging
from app.services.matcher import find_best_document
from app.services.storage import LocalStorageService
from app.services.whatsapp import WhatsAppSender
from app.stores import ResilientMetadataStore, StoreUnavailableError

ALICE = "whatsapp:+15550001111"
PASSWORD = "review-fixes-pass-123"


class RecordingSender:
    def __init__(self) -> None:
        self.enabled = True
        self.last_error: str | None = None
        self.text_calls: list[dict] = []
        self.media_calls: list[dict] = []
        self._counter = 0

    def _sid(self) -> str:
        self._counter += 1
        return f"SM{self._counter:04d}"

    def send_text(self, to_number: str, body: str, status_callback: str | None = None) -> str | None:
        self.text_calls.append({"to": to_number, "body": body})
        return self._sid()

    def send_media(self, to_number: str, body: str, media_url: str, status_callback: str | None = None) -> str | None:
        self.media_calls.append({"to": to_number, "body": body, "media_url": media_url})
        return self._sid()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


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


def _enable_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashboard_auth_enabled", True)
    monkeypatch.setattr(settings, "dashboard_username", "owner")
    monkeypatch.setattr(settings, "dashboard_password", PASSWORD)


def _login(client: TestClient, password: str = PASSWORD, **kwargs):
    return client.post("/login", data={"username": "owner", "password": password}, follow_redirects=False, **kwargs)


def _upload(client: TestClient, file_name: str, tags: str = "", category: str = "", private: bool = False) -> dict:
    response = client.post(
        "/upload",
        data={"doc_category": category, "tags": tags, "is_private": "true" if private else "false"},
        files={"file": (file_name, b"%PDF-1.4 review", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    return response.json()["document"]


def _webhook(client: TestClient, body: str, sender: str = ALICE) -> dict:
    response = client.post("/webhook", data={"From": sender, "Body": body})
    assert response.status_code == 200, response.text
    return response.json()


def _doc(tags: list[str], category: str = "general", file_name: str = "file.pdf", **extra) -> DocumentMetadata:
    return DocumentMetadata(
        file_name=file_name, file_type="pdf", doc_category=category, tags=tags, storage_path="unused.pdf", **extra
    )


def _tagged_doc(file_name: str, category: str = "") -> DocumentMetadata:
    tags = tagging.derive_final_tags([], file_name, category or "general", "pdf")
    final_category, _, _ = tagging.choose_final_category(category, tags)
    return _doc(tags, final_category, file_name)


# --------------------------------------------------------------------------- login rate limiting


def test_rotating_left_most_forwarded_for_does_not_reset_the_lock(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    monkeypatch.setattr(main_module, "login_rate_limiter", security.LoginRateLimiter(max_attempts=3, lockout_seconds=600))

    statuses = [
        _login(client, password="nope", headers={"X-Forwarded-For": f"10.0.{i}.1, 6.6.6.6"}).status_code
        for i in range(5)
    ]
    assert statuses == [401, 401, 401, 429, 429]
    # The right password from the locked real address is refused, whatever the spoofed first hop says.
    assert _login(client, headers={"X-Forwarded-For": "1.2.3.4, 6.6.6.6"}).status_code == 429
    # A spoofed first hop cannot lock out somebody else either.
    assert _login(client, headers={"X-Forwarded-For": "6.6.6.6, 7.7.7.7"}).status_code == 303


def test_account_wide_limit_stops_rotating_addresses(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    monkeypatch.setattr(
        main_module, "login_account_rate_limiter", security.LoginRateLimiter(max_attempts=4, lockout_seconds=600)
    )

    statuses = [
        _login(client, password="nope", headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(6)
    ]
    assert statuses == [401, 401, 401, 401, 429, 429]
    assert _login(client, headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 429


# --------------------------------------------------------------------------- body limits


def test_unauthenticated_upload_is_rejected_before_the_body_is_read(monkeypatch) -> None:
    _enable_auth(monkeypatch)
    receive_calls: list[int] = []
    sent: list[dict] = []

    async def receive() -> dict:
        receive_calls.append(1)
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/upload",
        "raw_path": b"/upload",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"multipart/form-data; boundary=xyz")],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    asyncio.run(main_module.app(scope, receive, send))

    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 401
    assert receive_calls == []


def test_oversized_bodies_are_rejected_with_413(client, monkeypatch) -> None:
    too_big = b"Body=" + b"a" * (70 * 1024)
    response = client.post(
        "/webhook", content=too_big, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 413

    login = client.post("/login", content=too_big, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert login.status_code == 413

    monkeypatch.setattr(settings, "max_upload_mb", 1)
    upload = client.post(
        "/upload",
        files={"file": ("big.pdf", b"x" * (2 * 1024 * 1024), "application/pdf")},
    )
    assert upload.status_code == 413


def test_chunked_body_over_the_limit_is_rejected(client) -> None:
    def chunks():
        for _ in range(100):
            yield b"a" * 1024

    response = client.post(
        "/webhook", content=chunks(), headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 413


def test_webhook_rejects_multipart_bodies(client) -> None:
    response = client.post(
        "/webhook",
        data={"From": ALICE, "Body": "resume"},
        files={"junk": ("junk.bin", b"x" * 10, "application/octet-stream")},
    )
    assert response.status_code == 415


# --------------------------------------------------------------------------- Twilio error masking / timeouts


def test_twilio_error_text_masks_recipient_number() -> None:
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: (_ for _ in ()).throw(
                TwilioRestException(
                    400, "https://api.twilio.com", msg="The 'To' number whatsapp:+919876543210 is not valid.", code=21211
                )
            )
        )
    )
    sender = WhatsAppSender("AC1", "tok", "+14155238886", client=client, sleep=lambda _: None)
    assert sender.send_text("+919876543210", "hi") is None
    assert "9876543210" not in (sender.last_error or "")
    assert "+91******3210" in sender.last_error
    assert "code=21211" in sender.last_error


def test_send_retries_stop_at_the_deadline() -> None:
    clock = FakeClock()
    attempts: list[int] = []

    def create(**kwargs):
        attempts.append(1)
        clock.now += 6  # each attempt takes 6 s (e.g. a slow connection)
        raise ConnectionError("slow")

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    sender = WhatsAppSender(
        "AC1", "tok", "+14155238886", retries=5, client=client, sleep=lambda _: None, clock=clock, deadline_seconds=10
    )
    assert sender.send_text("+911234567890", "hi") is None
    assert len(attempts) == 2


# --------------------------------------------------------------------------- sessions


def test_password_change_invalidates_existing_sessions(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    assert _login(client).status_code == 303
    assert client.get("/setup/status").status_code == 200

    monkeypatch.setattr(settings, "dashboard_password", "a-new-password")
    assert client.get("/setup/status").status_code == 401


def test_logout_survives_a_restart(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    assert _login(client).status_code == 303
    token = client.cookies.get("instaretriv_session")
    client.post("/logout", follow_redirects=False)

    replay = TestClient(main_module.app)
    replay.cookies.set("instaretriv_session", token)
    assert replay.get("/setup/status").status_code == 401

    # Simulate a restart: the in-memory set is empty, then reloaded from the request log.
    main_module._revoked_session_tokens.clear()
    main_module._seed_revoked_sessions()
    assert replay.get("/setup/status").status_code == 401
    logout_entries = main_module.request_logs.latest_by_type("logout", limit=10)
    assert logout_entries and token not in str(logout_entries)


# --------------------------------------------------------------------------- WhatsApp confirmation replies


@pytest.mark.parametrize("reply", ["yes please", "Yes 👍", "ok send", "yes, send it", "Yes.", "1"])
def test_natural_confirmations_deliver_the_private_document(client, fake_sender, reply) -> None:
    _upload(client, "pan_card.pdf", tags="pan,card", category="id", private=True)
    assert "private document" in _webhook(client, "send my pan card")["message"]

    result = _webhook(client, reply)
    assert result["message"] == "Document found and sent to your WhatsApp."
    assert len(fake_sender.media_calls) == 1


@pytest.mark.parametrize("reply", ["no thanks", "Cancel 🙏", "stop please"])
def test_natural_cancellations_cancel_the_request(client, fake_sender, reply) -> None:
    _upload(client, "pan_card.pdf", tags="pan,card", category="id", private=True)
    _webhook(client, "send my pan card")

    assert _webhook(client, reply)["message"] == "Private request canceled."
    assert fake_sender.media_calls == []


def test_unsearchable_reply_keeps_the_challenge_and_reprompts(client, fake_sender) -> None:
    _upload(client, "pan_card.pdf", tags="pan,card", category="id", private=True)
    _webhook(client, "send my pan card")

    reprompt = _webhook(client, "thanks")
    assert "private document" in reprompt["message"]
    assert main_module.normalize_phone(ALICE) in main_module._private_access_challenges

    assert _webhook(client, "yes")["message"] == "Document found and sent to your WhatsApp."


def test_yes_without_a_challenge_is_explained_even_with_filler(client, fake_sender) -> None:
    assert "No pending private confirmation" in _webhook(client, "yes please")["message"]


def test_store_outage_during_yes_keeps_the_challenge(client, fake_sender, monkeypatch) -> None:
    _upload(client, "pan_card.pdf", tags="pan,card", category="id", private=True)
    _webhook(client, "send my pan card")

    real_get = main_module.repository.get_by_id

    def down(document_id):
        raise StoreUnavailableError("cluster unreachable")

    monkeypatch.setattr(main_module.repository, "get_by_id", down)
    assert "can't reach its document database" in _webhook(client, "yes")["message"]

    monkeypatch.setattr(main_module.repository, "get_by_id", real_get)
    assert _webhook(client, "yes")["message"] == "Document found and sent to your WhatsApp."


def test_expired_and_invalidated_replies_record_their_sids(client, fake_sender, clock) -> None:
    _upload(client, "pan_card.pdf", tags="pan,card", category="id", private=True)
    _webhook(client, "send my pan card")
    clock.now += 3600
    assert "expired" in _webhook(client, "yes")["message"]

    audits = [entry for entry in main_module.request_logs.latest(limit=100) if entry.get("type") == "private-access-audit"]
    expired = [entry for entry in audits if entry.get("status") == "challenge-expired"]
    assert expired and expired[-1]["twilio_sid"] == "SM0002"
    delivery_sids = [entry["twilio_sid"] for entry in client.get("/logs/delivery").json()]
    assert "SM0002" in delivery_sids

    # Invalidated: the document is archived while the challenge is pending.
    _webhook(client, "send my pan card")
    doc_id = main_module._private_access_challenges[main_module.normalize_phone(ALICE)]["doc_id"]
    main_module.repository.deactivate(doc_id)
    assert "no longer available" in _webhook(client, "yes")["message"]
    audits = [entry for entry in main_module.request_logs.latest(limit=100) if entry.get("type") == "private-access-audit"]
    invalidated = [entry for entry in audits if entry.get("status") == "challenge-invalidated"]
    assert invalidated and invalidated[-1]["twilio_sid"] == "SM0004"


# --------------------------------------------------------------------------- matching


@pytest.mark.parametrize("query", ["aadhaar card", "voter card", "ration card", "debit card", "voter id"])
def test_generic_words_alone_do_not_return_another_card(query) -> None:
    pan = _tagged_doc("PAN Card.pdf", "id")
    assert find_best_document(query, [pan]).found is False


@pytest.mark.parametrize("query", ["birth certificate", "hsc certificate", "12th certificate", "income certificate"])
def test_generic_words_alone_do_not_return_another_certificate(query) -> None:
    ssc = _tagged_doc("SSC_Certificate.pdf", "certificate")
    assert find_best_document(query, [ssc]).found is False


@pytest.mark.parametrize(
    "query, expected",
    [
        ("pan card", "PAN Card.pdf"),
        ("ssc certificate", "SSC_Certificate.pdf"),
        ("10th certificate", "SSC_Certificate.pdf"),
        ("adhar card", "Aadhaar Card.pdf"),
        ("resme", "Resume.pdf"),
        ("mark sheet", "Marksheet.pdf"),
    ],
)
def test_intended_matches_still_work(query, expected) -> None:
    documents = [
        _tagged_doc("PAN Card.pdf", "id"),
        _tagged_doc("SSC_Certificate.pdf", "certificate"),
        _tagged_doc("Aadhaar Card.pdf", ""),
        _tagged_doc("Resume.pdf", "resume"),
        _tagged_doc("Marksheet.pdf", ""),
    ]
    result = find_best_document(query, documents)
    assert result.found is True
    assert result.document.file_name == expected


def test_ambiguous_id_card_query_returns_an_id_card_newest_first() -> None:
    # Both PAN and Aadhaar are categorised "id", so "send my id card" ties; the newest upload wins.
    from datetime import timedelta

    pan = _tagged_doc("PAN Card.pdf", "id")
    aadhaar = _tagged_doc("Aadhaar Card.pdf", "")
    aadhaar = aadhaar.model_copy(update={"uploaded_at": pan.uploaded_at + timedelta(seconds=5)})

    assert find_best_document("send my id card", [pan, aadhaar]).document.file_name == "Aadhaar Card.pdf"
    newer_pan = pan.model_copy(update={"uploaded_at": aadhaar.uploaded_at + timedelta(seconds=5)})
    assert find_best_document("send my id card", [newer_pan, aadhaar]).document.file_name == "PAN Card.pdf"


def test_fuzzy_threshold_rejects_near_but_different_words() -> None:
    # fuzz.ratio('password', 'passport') == 75 and ('statement', 'settlement') ~= 84: both below 85.
    assert find_best_document("send password", [_doc(["passport"], "passport")]).found is False
    assert find_best_document("statement", [_doc(["settlement"], "settlement")]).found is False


def test_full_phrase_bonus_decides_between_split_and_phrase_tags() -> None:
    now = datetime.now(timezone.utc)
    phrase = _doc(["pan card"], file_name="phrase.pdf", uploaded_at=now - timedelta(days=1))
    split = _doc(["pan", "card"], file_name="split.pdf", uploaded_at=now)
    assert find_best_document("pan card", [phrase, split]).document.file_name == "phrase.pdf"


def test_tie_break_handles_naive_timestamps() -> None:
    naive = DocumentMetadata.model_validate(
        {"file_name": "old.pdf", "file_type": "pdf", "doc_category": "resume", "tags": ["resume"],
         "storage_path": "a.pdf", "uploaded_at": "2025-01-01T10:00:00"}
    )
    aware = _doc(["resume"], "resume", "new.pdf", uploaded_at=datetime(2025, 6, 1, tzinfo=timezone.utc))
    assert naive.uploaded_at.tzinfo is not None
    assert find_best_document("resume", [naive, aware]).document.file_name == "new.pdf"


# --------------------------------------------------------------------------- stores


def test_non_utf8_metadata_and_log_files_are_quarantined(tmp_path) -> None:
    (tmp_path / "m.json").write_bytes(b"\xff\xfe\x00garbage\x8f\x90")
    (tmp_path / "l.json").write_bytes(b"[\x8f\x90]")

    repository = MetadataRepository(str(tmp_path / "m.json"))
    assert repository.list_active() == []
    logs = RequestLogRepository(str(tmp_path / "l.json"))
    assert logs.latest() == []
    logs.add({"type": "webhook"})
    assert [entry["type"] for entry in logs.latest()] == ["webhook"]

    names = sorted(path.name for path in tmp_path.iterdir())
    assert any(name.startswith("m.json.corrupt-") for name in names)
    assert any(name.startswith("l.json.corrupt-") for name in names)


class _SwitchableRepository:
    def __init__(self) -> None:
        self.down = False
        self.calls = 0

    def _check(self) -> None:
        self.calls += 1
        if self.down:
            raise ConnectionError("cluster down")

    def ping(self) -> None:
        self._check()

    def list_active(self):
        self._check()
        return []


def test_status_probes_detect_outage_and_recovery_without_traffic() -> None:
    clock = FakeClock()
    repository = _SwitchableRepository()
    store = ResilientMetadataStore(lambda: repository, backend_name="mongo", retry_seconds=30, clock=clock)
    assert store.status()["available"] is True

    repository.down = True
    clock.now += 6
    assert store.status()["available"] is False

    repository.down = False
    clock.now += 31
    assert store.status()["available"] is True


def test_open_circuit_fails_fast_without_calling_the_backend() -> None:
    clock = FakeClock()
    repository = _SwitchableRepository()
    store = ResilientMetadataStore(lambda: repository, backend_name="mongo", retry_seconds=30, clock=clock)
    repository.down = True
    with pytest.raises(StoreUnavailableError):
        store.list_active()
    calls = repository.calls

    for _ in range(5):
        with pytest.raises(StoreUnavailableError):
            store.list_active()
    assert repository.calls == calls

    repository.down = False
    clock.now += 31
    assert store.list_active() == []
    assert store.status()["available"] is True


# --------------------------------------------------------------------------- health / setup / files / upload


def test_health_reports_unusable_storage_but_stays_ready(client, monkeypatch) -> None:
    unavailable = main_module.UnavailableStorageService("Cloudinary is not configured", settings.upload_dir, 1024)
    monkeypatch.setattr(main_module, "storage_service", unavailable)
    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert health["checks"] == {"metadata_store": "ok", "storage": "unavailable"}
    assert client.get("/ready").status_code == 200


def test_setup_status_warns_when_request_logs_are_not_in_mongo(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "metadata_backend", "mongo")
    warnings = client.get("/setup/status").json()["warnings"]
    assert any("Request logs are kept in a local json store" in warning for warning in warnings)


def test_head_on_files_returns_headers(client, monkeypatch) -> None:
    document = _upload(client, "resume.pdf", tags="resume", category="resume")
    _enable_auth(monkeypatch)
    exp, sig = security.sign_file_access(document["id"], security.get_app_secret(), 300)

    response = client.head(f"/files/{document['id']}", params={"exp": exp, "sig": sig})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == b""
    assert client.head(f"/files/{document['id']}").status_code == 403


def test_upload_checks_the_store_before_saving_the_file(client, monkeypatch) -> None:
    saved: list[str] = []

    class SpyStorage(LocalStorageService):
        async def save(self, upload_file):
            saved.append(upload_file.filename)
            return await super().save(upload_file)

    monkeypatch.setattr(main_module, "storage_service", SpyStorage(settings.upload_dir))

    def down():
        raise StoreUnavailableError("cluster unreachable")

    monkeypatch.setattr(main_module.repository, "ping", down)
    response = client.post("/upload", files={"file": ("resume.pdf", b"%PDF", "application/pdf")})
    assert response.status_code == 503
    assert saved == []
