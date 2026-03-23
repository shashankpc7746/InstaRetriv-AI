from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.repository import MetadataRepository
from app.request_log_repository import RequestLogRepository
from app.services.storage import LocalStorageService


def _configure_test_state(tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    metadata_file = tmp_path / "metadata.json"
    request_log_file = tmp_path / "request_logs.json"

    main_module.repository = MetadataRepository(str(metadata_file))
    main_module.request_logs = RequestLogRepository(str(request_log_file))
    main_module.storage_service = LocalStorageService(str(upload_dir))


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
