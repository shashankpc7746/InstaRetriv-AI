import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.services.whatsapp import WhatsAppSender
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
    main_module.whatsapp_sender = WhatsAppSender(account_sid="", auth_token="", sender="")
    main_module.settings.require_twilio_signature = False
    main_module.settings.authorized_senders = ""
    main_module.settings.public_base_url = ""
    main_module.settings.dashboard_auth_enabled = False


def test_setup_status_endpoint_has_expected_keys(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.get("/setup/status")
    assert response.status_code == 200
    payload = response.json()
    assert "twilio_sid_set" in payload
    assert "twilio_auth_token_set" in payload
    assert "twilio_whatsapp_from_set" in payload
    assert "public_base_url_set" in payload
    assert "require_twilio_signature" in payload


def test_upload_then_get_document_flow(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    upload_response = client.post(
        "/upload",
        data={"doc_category": "resume", "tags": "resume,cv,latest"},
        files={"file": ("resume.pdf", b"dummy-pdf-content", "application/pdf")},
    )

    assert upload_response.status_code == 200
    uploaded = upload_response.json()["document"]
    assert uploaded["file_name"] == "resume.pdf"

    retrieval_response = client.get("/get-document", params={"query": "send my cv"})
    assert retrieval_response.status_code == 200
    retrieval_payload = retrieval_response.json()
    assert retrieval_payload["found"] is True
    assert retrieval_payload["document"]["id"] == uploaded["id"]


def test_list_and_archive_document(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    upload_response = client.post(
        "/upload",
        data={"doc_category": "certificate", "tags": "certificate,final"},
        files={"file": ("cert.pdf", b"dummy-certificate", "application/pdf")},
    )
    assert upload_response.status_code == 200
    doc_id = upload_response.json()["document"]["id"]

    list_response = client.get("/documents", params={"active_only": "true"})
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    archive_response = client.delete(f"/documents/{doc_id}")
    assert archive_response.status_code == 200
    assert archive_response.json()["message"] == "Document archived"

    post_archive_list = client.get("/documents", params={"active_only": "true"})
    assert post_archive_list.status_code == 200
    assert len(post_archive_list.json()) == 0


def test_upload_auto_extracts_filename_tags(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    upload_response = client.post(
        "/upload",
        data={"doc_category": "id", "tags": "important"},
        files={"file": ("pan_card_2024_final.pdf", b"dummy-pdf-content", "application/pdf")},
    )

    assert upload_response.status_code == 200
    uploaded = upload_response.json()["document"]
    assert "important" in uploaded["tags"]
    assert "pan" in uploaded["tags"]
    assert "card" in uploaded["tags"]
    assert "2024" in uploaded["tags"]
    assert "final" not in uploaded["tags"]


def test_upload_auto_tags_do_not_duplicate_manual_tags(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    upload_response = client.post(
        "/upload",
        data={"doc_category": "resume", "tags": "resume, cv"},
        files={"file": ("resume_cv_latest.pdf", b"dummy-pdf-content", "application/pdf")},
    )

    assert upload_response.status_code == 200
    uploaded = upload_response.json()["document"]
    assert uploaded["tags"].count("resume") == 1
    assert uploaded["tags"].count("cv") == 1


def test_upload_suggests_category_for_generic_input(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    upload_response = client.post(
        "/upload",
        data={"doc_category": "other", "tags": "important"},
        files={"file": ("resume_cv_2026.pdf", b"dummy-pdf-content", "application/pdf")},
    )

    assert upload_response.status_code == 200
    uploaded = upload_response.json()["document"]
    assert uploaded["doc_category"] == "resume"


def test_upload_keeps_specific_category_even_if_suggestion_exists(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    upload_response = client.post(
        "/upload",
        data={"doc_category": "invoice", "tags": "urgent"},
        files={"file": ("resume_profile.pdf", b"dummy-pdf-content", "application/pdf")},
    )

    assert upload_response.status_code == 200
    uploaded = upload_response.json()["document"]
    assert uploaded["doc_category"] == "invoice"


def test_private_upload_requires_access_code(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    upload_response = client.post(
        "/upload",
        data={"doc_category": "id", "tags": "pan", "is_private": "true"},
        files={"file": ("pan_card.pdf", b"dummy-pdf-content", "application/pdf")},
    )

    assert upload_response.status_code == 400
    assert "Set your profile private access code first" in upload_response.json()["detail"]


def test_profile_private_access_code_can_be_set(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.post(
        "/profile/private-access-code",
        data={
            "private_access_code": "1234",
            "confirm_private_access_code": "1234",
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Private access code updated."


def test_private_upload_sets_private_metadata(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    set_code_response = client.post(
        "/profile/private-access-code",
        data={
            "private_access_code": "1234",
            "confirm_private_access_code": "1234",
        },
    )
    assert set_code_response.status_code == 200

    upload_response = client.post(
        "/upload",
        data={
            "doc_category": "id",
            "tags": "pan",
            "is_private": "true",
        },
        files={"file": ("pan_card.pdf", b"dummy-pdf-content", "application/pdf")},
    )

    assert upload_response.status_code == 200
    uploaded = upload_response.json()["document"]
    assert uploaded["is_private"] is True
    assert "access_code_hash" not in uploaded


def test_legacy_per_document_private_code_is_ignored(tmp_path: Path) -> None:
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    set_code_response = client.post(
        "/profile/private-access-code",
        data={
            "private_access_code": "2468",
            "confirm_private_access_code": "2468",
        },
    )
    assert set_code_response.status_code == 200

    upload_response = client.post(
        "/upload",
        data={"doc_category": "id", "tags": "pan", "is_private": "true"},
        files={"file": ("pan_card.pdf", b"dummy-pdf-content", "application/pdf")},
    )
    assert upload_response.status_code == 200

    metadata_path = tmp_path / "metadata.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data[0]["access_code_hash"] = "legacy-hash-value"
    metadata_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    denial_response = client.post(
        "/webhook",
        data={
            "From": "whatsapp:+12345678901",
            "Body": "send my pan card code 1111",
        },
    )

    assert denial_response.status_code == 200
    assert "Invalid passcode" in denial_response.json()["message"]
