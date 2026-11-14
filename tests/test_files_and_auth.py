"""/files access control, dashboard auth/login/logout, upload limits, health, setup status and API docs."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import security
from app.config import Settings, settings
from app.schemas import DocumentMetadata
from app.services.storage import LocalStorageService, StorageUnavailableError, StoredFileNotFoundError
from app.stores import ResilientMetadataStore, StoreUnavailableError

PASSWORD = "correct horse battery staple"


@pytest.fixture
def client() -> TestClient:
    return TestClient(main_module.app)


def _upload(client: TestClient, file_name: str = "resume.pdf", content: bytes = b"%PDF-1.4 hello", **data) -> dict:
    response = client.post(
        "/upload",
        data={"doc_category": data.get("category", "resume"), "tags": data.get("tags", "resume"),
              "is_private": "true" if data.get("private") else "false"},
        files={"file": (file_name, content, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    return response.json()["document"]


def _signed_query(doc_id: str, ttl: int = 300, now: float | None = None) -> dict:
    exp, sig = security.sign_file_access(doc_id, security.get_app_secret(), ttl, now=now)
    return {"exp": str(exp), "sig": sig}


def _enable_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashboard_auth_enabled", True)
    monkeypatch.setattr(settings, "dashboard_username", "owner")
    monkeypatch.setattr(settings, "dashboard_password", PASSWORD)


def _login(client: TestClient, password: str = PASSWORD, **kwargs):
    return client.post("/login", data={"username": "owner", "password": password}, follow_redirects=False, **kwargs)


def _add_remote_doc(storage_path: str, file_type: str = "pdf", file_name: str = "remote.pdf") -> DocumentMetadata:
    document = DocumentMetadata(
        file_name=file_name, file_type=file_type, doc_category="general", tags=["remote"], storage_path=storage_path
    )
    main_module.repository.add(document)
    return document


class FakeRemoteStorage(LocalStorageService):
    """Local storage whose iter_remote is scripted."""

    def __init__(self, upload_dir: str, outcome) -> None:
        super().__init__(upload_dir)
        self.outcome = outcome
        self.requested: list[str] = []

    def iter_remote(self, storage_path: str):
        self.requested.append(storage_path)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return iter(self.outcome)


# --------------------------------------------------------------------------- /files


def test_files_with_valid_signature_streams_local_file_inline(client, monkeypatch) -> None:
    uploaded = _upload(client, "My Résumé.pdf", b"%PDF-1.4 secret bytes")
    _enable_auth(monkeypatch)

    response = client.get(f"/files/{uploaded['id']}", params=_signed_query(uploaded["id"]))

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 secret bytes"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert "no-store" in response.headers["cache-control"]


def test_files_without_session_or_signature_is_forbidden(client, monkeypatch) -> None:
    uploaded = _upload(client)
    _enable_auth(monkeypatch)

    assert client.get(f"/files/{uploaded['id']}").status_code == 403
    # Unknown ids are not distinguishable from real ones without credentials.
    assert client.get("/files/does-not-exist").status_code == 403


def test_files_expired_signature_is_forbidden(client, monkeypatch) -> None:
    uploaded = _upload(client)
    _enable_auth(monkeypatch)

    params = _signed_query(uploaded["id"], ttl=10, now=time.time() - 1000)
    assert client.get(f"/files/{uploaded['id']}", params=params).status_code == 403


def test_files_tampered_signature_is_forbidden(client, monkeypatch) -> None:
    first = _upload(client, "a.pdf")
    second = _upload(client, "b.pdf")
    _enable_auth(monkeypatch)

    params = _signed_query(first["id"])
    tampered = dict(params, sig=("0" if params["sig"][0] != "0" else "1") + params["sig"][1:])
    assert client.get(f"/files/{first['id']}", params=tampered).status_code == 403
    later_exp = dict(params, exp=str(int(params["exp"]) + 3600))
    assert client.get(f"/files/{first['id']}", params=later_exp).status_code == 403
    # A signature for one document never opens another.
    assert client.get(f"/files/{second['id']}", params=params).status_code == 403


def test_files_with_dashboard_session(client, monkeypatch) -> None:
    uploaded = _upload(client, "photo.png", b"\x89PNG fake")
    _enable_auth(monkeypatch)
    assert _login(client).status_code == 303

    response = client.get(f"/files/{uploaded['id']}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_files_archived_document_is_404_even_with_signature(client, monkeypatch) -> None:
    uploaded = _upload(client)
    params = _signed_query(uploaded["id"])
    assert client.delete(f"/documents/{uploaded['id']}").status_code == 200
    _enable_auth(monkeypatch)

    response = client.get(f"/files/{uploaded['id']}", params=params)

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_files_missing_local_file_is_404(client) -> None:
    uploaded = _upload(client)
    Path(main_module.repository.get_by_id(uploaded["id"]).storage_path).unlink()

    response = client.get(f"/files/{uploaded['id']}", params=_signed_query(uploaded["id"]))

    assert response.status_code == 404
    assert response.json()["detail"] == "Stored file missing"


def test_files_remote_streams_with_content_type(client, monkeypatch, tmp_path) -> None:
    storage = FakeRemoteStorage(str(tmp_path), [b"chunk-1", b"chunk-2"])
    monkeypatch.setattr(main_module, "storage_service", storage)
    document = _add_remote_doc("cloudinary://raw/authenticated/instaretriv/abc.docx", "docx", "cv.docx")

    response = client.get(f"/files/{document.id}", params=_signed_query(document.id))

    assert response.status_code == 200
    assert response.content == b"chunk-1chunk-2"
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert 'filename="cv.docx"' in response.headers["content-disposition"]
    assert storage.requested == ["cloudinary://raw/authenticated/instaretriv/abc.docx"]


def test_files_remote_not_found_is_404(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main_module, "storage_service", FakeRemoteStorage(str(tmp_path), StoredFileNotFoundError("gone")))
    document = _add_remote_doc("cloudinary://raw/authenticated/instaretriv/gone.pdf")

    response = client.get(f"/files/{document.id}", params=_signed_query(document.id))

    assert response.status_code == 404


def test_files_remote_unavailable_is_502(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        main_module, "storage_service", FakeRemoteStorage(str(tmp_path), StorageUnavailableError("HTTP 503"))
    )
    document = _add_remote_doc("https://res.cloudinary.com/demo/raw/upload/v1/old.pdf")

    response = client.get(f"/files/{document.id}", params=_signed_query(document.id))

    assert response.status_code == 502


# --------------------------------------------------------------------------- dashboard auth


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/get-document?query=resume"),
        ("GET", "/documents"),
        ("DELETE", "/documents/some-id"),
        ("GET", "/logs/recent"),
        ("GET", "/logs/delivery"),
        ("GET", "/logs/delivery/summary"),
        ("GET", "/setup/status"),
    ],
)
def test_dashboard_routes_require_auth_when_enabled(client, monkeypatch, method, path) -> None:
    _enable_auth(monkeypatch)
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_upload_requires_auth_when_enabled(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    response = client.post("/upload", files={"file": ("resume.pdf", b"x", "application/pdf")})
    assert response.status_code == 401


def test_public_routes_stay_public_when_auth_enabled(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/login").status_code == 200
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 303 and root.headers["location"] == "/login"


def test_login_page_and_dashboard_html(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    login_page = client.get("/login")
    assert login_page.headers["content-type"].startswith("text/html")
    assert 'name="viewport"' in login_page.text

    _login(client)
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "owner" in dashboard.text
    assert "frame-ancestors 'none'" in dashboard.headers["content-security-policy"]
    # Signed-in users are sent from /login to the dashboard.
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"


def test_login_without_configured_password_is_503_html(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "dashboard_auth_enabled", True)
    monkeypatch.setattr(settings, "dashboard_password", "")

    response = client.post("/login", data={"username": "admin", "password": ""}, follow_redirects=False)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert "DASHBOARD_PASSWORD" in response.text
    assert "set-cookie" not in response.headers


def test_wrong_password_is_401_html(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)

    response = _login(client, password="wrong")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("text/html")
    assert "Incorrect username or password." in response.text
    assert "set-cookie" not in response.headers


def test_wrong_username_is_rejected(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    response = client.post("/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 401


def test_login_rate_limit_locks_client_ip(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    monkeypatch.setattr(main_module, "login_rate_limiter", security.LoginRateLimiter(max_attempts=3, lockout_seconds=600))
    attacker = {"headers": {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}}

    statuses = [_login(client, password="nope", **attacker).status_code for _ in range(3)]
    assert statuses == [401, 401, 401]

    locked = _login(client, **attacker)  # even the right password is refused while locked
    assert locked.status_code == 429
    assert locked.headers["content-type"].startswith("text/html")
    assert int(locked.headers["retry-after"]) > 0
    assert "set-cookie" not in locked.headers

    other_client = _login(client, headers={"X-Forwarded-For": "198.51.100.7"})
    assert other_client.status_code == 303


def test_successful_login_resets_failures(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    monkeypatch.setattr(main_module, "login_rate_limiter", security.LoginRateLimiter(max_attempts=2, lockout_seconds=600))

    assert _login(client, password="nope").status_code == 401
    assert _login(client).status_code == 303
    assert _login(client, password="nope").status_code == 401
    assert _login(client).status_code == 303


def test_session_cookie_flags_in_development_http(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    cookie = _login(client).headers["set-cookie"]
    assert "instaretriv_session=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Secure" not in cookie


def test_session_cookie_secure_in_production(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    monkeypatch.setattr(settings, "app_env", "production")
    assert "Secure" in _login(client).headers["set-cookie"]


def test_session_cookie_secure_behind_https_proxy(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    cookie = _login(client, headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"]
    assert "Secure" in cookie


def test_logout_revokes_the_session_token(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    _login(client)
    token = client.cookies.get("instaretriv_session")
    assert token
    assert client.get("/documents").status_code == 200

    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"

    # Replaying the old cookie value does not work any more.
    replay = TestClient(main_module.app, cookies={"instaretriv_session": token})
    assert replay.get("/documents").status_code == 401


def test_get_logout_also_works(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    _login(client)
    token = client.cookies.get("instaretriv_session")
    assert client.get("/logout", follow_redirects=False).status_code == 303
    assert TestClient(main_module.app, cookies={"instaretriv_session": token}).get("/documents").status_code == 401


def test_expired_or_forged_session_is_rejected(client, monkeypatch) -> None:
    _enable_auth(monkeypatch)
    ttl = settings.dashboard_session_ttl_minutes * 60
    expired = security.create_session_token(security.get_app_secret(), now=time.time() - ttl - 120)
    forged = security.create_session_token("some-other-secret")

    for token in (expired, forged, "garbage"):
        assert TestClient(main_module.app, cookies={"instaretriv_session": token}).get("/documents").status_code == 401


# --------------------------------------------------------------------------- upload validation


def test_upload_too_large_is_413(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    monkeypatch.setattr(main_module, "storage_service", main_module.create_storage_service(settings))

    response = client.post(
        "/upload",
        data={"doc_category": "resume"},
        files={"file": ("big.pdf", b"x" * (1024 * 1024 + 1), "application/pdf")},
    )

    assert response.status_code == 413
    assert main_module.repository.list_all() == []


def test_upload_empty_file_is_400(client) -> None:
    response = client.post("/upload", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"tags": ",".join(f"t{i}" for i in range(21))}, "Too many tags"),
        ({"tags": "x" * 41}, "at most 40 characters"),
        ({"doc_category": "c" * 41}, "Category must be at most 40"),
    ],
)
def test_upload_rejects_oversized_metadata(client, data, expected) -> None:
    response = client.post("/upload", data=data, files={"file": ("a.pdf", b"x", "application/pdf")})
    assert response.status_code == 400
    assert expected in response.json()["detail"]


def test_upload_keeps_explicit_id_category(client) -> None:
    uploaded = _upload(client, "aadhaar.pdf", category="id", tags="aadhaar")
    assert uploaded["doc_category"] == "id"


def test_upload_strips_client_path_from_file_name(client) -> None:
    uploaded = _upload(client, "C:\\Users\\me\\Desktop\\resume.pdf")
    assert uploaded["file_name"] == "resume.pdf"


def test_upload_when_storage_unavailable_is_503(client, monkeypatch, tmp_path) -> None:
    unavailable = main_module.UnavailableStorageService("Cloudinary is not configured", str(tmp_path), 1024)
    monkeypatch.setattr(main_module, "storage_service", unavailable)

    response = client.post("/upload", files={"file": ("a.pdf", b"x", "application/pdf")})

    assert response.status_code == 503
    assert main_module.repository.list_all() == []


def test_upload_when_store_unavailable_is_503_and_stores_no_file(client, monkeypatch) -> None:
    def broken():
        raise StoreUnavailableError("down")

    monkeypatch.setattr(main_module, "repository", ResilientMetadataStore(broken, backend_name="mongo"))

    response = client.post("/upload", files={"file": ("a.pdf", b"x", "application/pdf")})

    assert response.status_code == 503
    assert "database" in response.json()["detail"]
    assert list(main_module.storage_service.upload_path.iterdir()) == []


def test_metadata_failure_removes_the_stored_file(client, monkeypatch) -> None:
    def failing_add(document):
        raise StoreUnavailableError("write failed")

    monkeypatch.setattr(main_module.repository, "add", failing_add)

    response = client.post("/upload", files={"file": ("a.pdf", b"x", "application/pdf")})

    assert response.status_code == 503
    assert [p for p in main_module.storage_service.upload_path.iterdir() if p.is_file()] == []


def test_storage_factory_never_silently_falls_back_in_production(tmp_path) -> None:
    upload_dir = str(tmp_path / "uploads")
    prod = Settings(_env_file=None, app_env="production", storage_backend="cloudinary", upload_dir=upload_dir)
    service = main_module.create_storage_service(prod)
    assert service.backend_name == "unavailable"
    assert main_module.storage_fallback_reason == "Cloudinary is not configured"

    dev = Settings(_env_file=None, app_env="development", storage_backend="cloudinary", upload_dir=upload_dir)
    assert main_module.create_storage_service(dev).backend_name == "local"
    assert main_module.storage_fallback_reason == "Cloudinary is not configured"

    plain = Settings(_env_file=None, app_env="production", storage_backend="local", upload_dir=upload_dir)
    assert main_module.create_storage_service(plain).backend_name == "local"
    assert main_module.storage_fallback_reason is None


# --------------------------------------------------------------------------- documents API


def test_documents_and_get_document_never_expose_storage_path(client) -> None:
    uploaded = _upload(client)

    listed = client.get("/documents").json()
    found = client.get("/get-document", params={"query": "resume"}).json()

    assert "storage_path" not in listed[0]
    assert listed[0]["storage_kind"] == "local"
    assert found["found"] is True
    assert found["document"]["id"] == uploaded["id"]
    assert "storage_path" not in found["document"]


def test_get_document_store_unavailable_is_503(client, monkeypatch) -> None:
    def broken():
        raise StoreUnavailableError("down")

    monkeypatch.setattr(main_module, "repository", ResilientMetadataStore(broken, backend_name="mongo"))
    assert client.get("/get-document", params={"query": "resume"}).status_code == 503
    assert client.get("/documents").status_code == 503


def test_archived_document_never_matches(client) -> None:
    uploaded = _upload(client)
    client.delete(f"/documents/{uploaded['id']}")
    assert client.get("/get-document", params={"query": "resume"}).json()["found"] is False


def test_logs_api_masks_legacy_raw_senders(client) -> None:
    main_module.request_logs.add({"type": "webhook", "sender": "whatsapp:+919876543210", "query": "resume"})
    entries = client.get("/logs/recent").json()
    assert entries[-1]["sender"] == "+91******3210"


# --------------------------------------------------------------------------- health, readiness, setup status, docs


def test_health_and_ready_when_store_ok(client) -> None:
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["checks"] == {"metadata_store": "ok", "storage": "ok"}
    assert client.get("/ready").status_code == 200


def test_health_degraded_and_not_ready_when_store_down(client, monkeypatch) -> None:
    def broken():
        raise StoreUnavailableError("cluster deleted")

    monkeypatch.setattr(main_module, "repository", ResilientMetadataStore(broken, backend_name="mongo"))

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["checks"]["metadata_store"] == "unavailable"
    ready = client.get("/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "degraded"


def test_setup_status_fields_and_development_warnings(client) -> None:
    status = client.get("/setup/status").json()

    for key in (
        "metadata_store",
        "storage_backend_active",
        "request_log_backend",
        "allowed_extensions",
        "max_upload_mb",
        "private_confirmation_ttl_seconds",
        "authorized_senders_count",
        "sender_allowlist_enforced",
        "twilio_signature_required",
        "app_secret_configured",
        "dashboard_login_available",
        "warnings",
    ):
        assert key in status
    assert "private_access_code_set" not in status
    assert status["metadata_store"]["available"] is True
    assert status["storage_backend_active"] == "local"
    assert status["allowed_extensions"] == ["pdf", "png", "jpg", "jpeg", "webp", "doc", "docx"]
    assert status["private_confirmation_ttl_seconds"] == 45
    assert status["sender_allowlist_enforced"] is False

    warnings = " | ".join(status["warnings"])
    assert "Dashboard sign-in is turned off" in warnings
    assert "any WhatsApp number can use the bot" in warnings
    assert "PUBLIC_BASE_URL is not set" in warnings
    assert "WhatsApp sending is off" in warnings
    assert "signature checking is off" in warnings
    assert "APP_SECRET_KEY" not in warnings  # configured in tests


def test_setup_status_production_warnings(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "app_secret_key", "")
    monkeypatch.setattr(settings, "dashboard_auth_enabled", True)
    monkeypatch.setattr(settings, "dashboard_password", "")
    monkeypatch.setattr(settings, "storage_backend", "cloudinary")
    monkeypatch.setattr(main_module, "storage_service", main_module.create_storage_service(settings))

    warnings = " | ".join(main_module._setup_warnings(main_module._metadata_store_status(), "unavailable"))

    assert "every WhatsApp number is blocked in production" in warnings
    assert "APP_SECRET_KEY is not set" in warnings
    assert "DASHBOARD_PASSWORD is not set" in warnings
    assert "uploads are disabled" in warnings
    assert "TWILIO_AUTH_TOKEN is empty" in warnings
    assert "METADATA_BACKEND=json in production" in warnings


def test_setup_status_reports_store_problems(client, monkeypatch) -> None:
    def broken():
        raise StoreUnavailableError("MONGODB_URI is empty")

    monkeypatch.setattr(main_module, "repository", ResilientMetadataStore(broken, backend_name="mongo"))
    status = client.get("/setup/status").json()
    assert status["metadata_store"]["available"] is False
    assert any("document database (mongo) is unavailable" in w for w in status["warnings"])

    fallback = ResilientMetadataStore(
        _json_repo,
        backend_name="json",
        fallback=True,
        fallback_reason="MongoDB unavailable: timeout",
    )
    monkeypatch.setattr(main_module, "repository", fallback)
    status = client.get("/setup/status").json()
    assert status["metadata_store"]["fallback"] is True
    assert any("temporary local JSON file" in w for w in status["warnings"])


def _json_repo():
    from app.repository import MetadataRepository

    return MetadataRepository(settings.metadata_file)


def test_api_docs_disabled_in_production() -> None:
    production = Settings(_env_file=None, app_env="production")
    assert main_module._api_docs_urls(production) == {"docs_url": None, "redoc_url": None, "openapi_url": None}

    development = Settings(_env_file=None, app_env="development")
    assert main_module._api_docs_urls(development)["docs_url"] == "/docs"

    forced = Settings(_env_file=None, app_env="production", enable_api_docs=True)
    assert main_module._api_docs_urls(forced)["openapi_url"] == "/openapi.json"


def test_security_headers_on_every_response(client) -> None:
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]
