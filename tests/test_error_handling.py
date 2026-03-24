"""
Tests for error handling and validation in API endpoints.
Covers invalid inputs, missing fields, and constraint violations.
"""
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.repository import MetadataRepository
from app.request_log_repository import RequestLogRepository
from app.services.storage import LocalStorageService


def _configure_test_state(tmp_path: Path) -> None:
    """Configure temporary storage for test isolation."""
    upload_dir = tmp_path / "uploads"
    metadata_file = tmp_path / "metadata.json"
    request_log_file = tmp_path / "request_logs.json"

    main_module.repository = MetadataRepository(str(metadata_file))
    main_module.request_logs = RequestLogRepository(str(request_log_file))
    main_module.storage_service = LocalStorageService(str(upload_dir))


def test_upload_missing_file_name(tmp_path: Path) -> None:
    """Test upload endpoint rejects file without name."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.post(
        "/upload",
        data={"doc_category": "resume", "tags": "resume"},
        files={"file": ("", b"content", "application/pdf")},  # Empty filename
    )

    # FastAPI returns 422 for validation errors on file upload
    assert response.status_code == 422


def test_upload_missing_tags(tmp_path: Path) -> None:
    """Test upload endpoint requires at least one tag."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.post(
        "/upload",
        data={"doc_category": "resume", "tags": ""},  # Empty tags
        files={"file": ("resume.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == 400
    assert "At least one tag is required" in response.json()["detail"]


def test_upload_missing_tags_with_only_whitespace(tmp_path: Path) -> None:
    """Test upload endpoint rejects tags that are only whitespace."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.post(
        "/upload",
        data={"doc_category": "resume", "tags": "   ,  ,   "},  # Only whitespace and commas
        files={"file": ("resume.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == 400
    assert "At least one tag is required" in response.json()["detail"]


def test_upload_unsupported_file_type(tmp_path: Path) -> None:
    """Test upload endpoint rejects unsupported file extensions."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.post(
        "/upload",
        data={"doc_category": "resume", "tags": "resume"},
        files={"file": ("script.exe", b"malware-content", "application/x-msdownload")},
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_get_document_with_empty_query(tmp_path: Path) -> None:
    """Test get-document endpoint handles empty query gracefully."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.get("/get-document", params={"query": ""})

    assert response.status_code == 200
    payload = response.json()
    assert payload["found"] is False  # Empty query should not match anything


def test_get_documents_with_invalid_active_only_param(tmp_path: Path) -> None:
    """Test get-documents endpoint accepts active_only parameter."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.get("/documents", params={"active_only": "false"})

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_logs_endpoint_with_invalid_limit(tmp_path: Path) -> None:
    """Test logs endpoint rejects invalid limit values."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    # Test limit too low
    response = client.get("/logs/recent", params={"limit": 0})
    assert response.status_code == 400
    assert "limit must be between 1 and 200" in response.json()["detail"]

    # Test limit too high
    response = client.get("/logs/recent", params={"limit": 500})
    assert response.status_code == 400
    assert "limit must be between 1 and 200" in response.json()["detail"]


def test_logs_endpoint_with_valid_limit(tmp_path: Path) -> None:
    """Test logs endpoint accepts valid limit values."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.get("/logs/recent", params={"limit": 50})
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_serve_nonexistent_document(tmp_path: Path) -> None:
    """Test file serving endpoint returns 404 for nonexistent document."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.get("/files/nonexistent-doc-id")

    assert response.status_code == 404
    assert "Document not found" in response.json()["detail"]


def test_archive_nonexistent_document(tmp_path: Path) -> None:
    """Test archive endpoint returns 404 for nonexistent document."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.delete("/documents/nonexistent-doc-id")

    assert response.status_code == 404
    assert "Document not found" in response.json()["detail"]


def test_all_endpoints_return_request_id_header(tmp_path: Path) -> None:
    """Test that all endpoints include X-Request-ID header in response."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    endpoints_to_test = [
        ("GET", "/health"),
        ("GET", "/setup/status"),
        ("GET", "/documents"),
        ("POST", "/webhook", {"From": "test", "Body": "test"}),
        ("GET", "/logs/recent"),
    ]

    for method, path, *data in endpoints_to_test:
        if method == "GET":
            response = client.get(path)
        else:
            form_data = data[0] if data else {}
            response = client.post(path, data=form_data)

        assert "X-Request-ID" in response.headers, f"Missing X-Request-ID in {method} {path}"
        assert response.headers["X-Request-ID"], f"Empty X-Request-ID in {method} {path}"


def test_health_endpoint_returns_ok(tmp_path: Path) -> None:
    """Test health endpoint returns proper status."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "app" in payload
    assert "env" in payload
