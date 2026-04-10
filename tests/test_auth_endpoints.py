from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.profile_settings_repository import ProfileSettingsRepository
from app.repository import MetadataRepository
from app.request_log_repository import RequestLogRepository
from app.services.storage import LocalStorageService


def _configure_test_state(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    metadata_file = tmp_path / "metadata.json"
    request_log_file = tmp_path / "request_logs.json"
    profile_settings_file = tmp_path / "profile_settings.json"

    main_module.repository = MetadataRepository(str(metadata_file))
    main_module.request_logs = RequestLogRepository(str(request_log_file))
    main_module.storage_service = LocalStorageService(str(upload_dir))
    main_module.profile_settings_repo = ProfileSettingsRepository(str(profile_settings_file))
    main_module.settings.require_twilio_signature = False
    main_module.settings.authorized_senders = ""
    main_module.settings.dashboard_auth_enabled = False


def test_dashboard_redirects_to_login_when_auth_enabled(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    with patch.object(main_module.settings, "dashboard_auth_enabled", True):
        response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_protected_endpoint_requires_login_when_auth_enabled(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    with patch.object(main_module.settings, "dashboard_auth_enabled", True):
        response = client.post(
            "/profile/private-access-code",
            data={
                "private_access_code": "1234",
                "confirm_private_access_code": "1234",
            },
        )

    assert response.status_code == 401
    assert "Authentication required" in response.json()["detail"]


def test_login_allows_upload_when_auth_enabled(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    with patch.object(main_module.settings, "dashboard_auth_enabled", True), patch.object(
        main_module.settings, "dashboard_username", "owner"
    ), patch.object(main_module.settings, "dashboard_password", "topsecret"):
        login_response = client.post(
            "/login",
            data={"username": "owner", "password": "topsecret"},
            follow_redirects=False,
        )

        assert login_response.status_code == 303
        assert login_response.headers["location"] == "/"

        upload_response = client.post(
            "/upload",
            data={"doc_category": "resume", "tags": "cv,resume"},
            files={"file": ("resume.pdf", b"dummy", "application/pdf")},
        )

    assert upload_response.status_code == 200
    payload = upload_response.json()
    assert payload["message"] == "Upload successful"
