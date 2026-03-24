"""
Tests for WhatsApp webhook endpoint.
Covers signature validation, sender filtering, document matching, and error scenarios.
"""
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
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


def _add_test_document(client: TestClient) -> str:
    """Upload a test document and return its ID."""
    response = client.post(
        "/upload",
        data={"doc_category": "resume", "tags": "resume,cv"},
        files={"file": ("resume.pdf", b"dummy-resume", "application/pdf")},
    )
    return response.json()["document"]["id"]


def test_webhook_with_document_match(tmp_path: Path) -> None:
    """Test webhook successfully finds and matches a document."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    doc_id = _add_test_document(client)

    webhook_response = client.post(
        "/webhook",
        data={
            "From": "whatsapp:+12345678901",
            "Body": "send my resume",
        },
    )

    assert webhook_response.status_code == 200
    payload = webhook_response.json()
    assert payload["matched_document_id"] == doc_id
    assert "Document found" in payload["message"]


def test_webhook_with_no_document_match(tmp_path: Path) -> None:
    """Test webhook returns appropriate message when no document matches."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    # Add a document but search for something unrelated
    _add_test_document(client)

    webhook_response = client.post(
        "/webhook",
        data={
            "From": "whatsapp:+12345678901",
            "Body": "xyz unrelated query",
        },
    )

    assert webhook_response.status_code == 200
    payload = webhook_response.json()
    assert "matched_document_id" not in payload or payload.get("matched_document_id") is None
    assert "Document not found" in payload["message"]


def test_webhook_with_missing_from_field(tmp_path: Path) -> None:
    """Test webhook handles missing From field gracefully."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    _add_test_document(client)

    webhook_response = client.post(
        "/webhook",
        data={
            "Body": "send my resume",
            # From field is intentionally missing
        },
    )

    assert webhook_response.status_code == 200
    # Empty From should still process but may not send
    payload = webhook_response.json()
    assert "message" in payload


def test_webhook_with_missing_body_field(tmp_path: Path) -> None:
    """Test webhook handles missing Body field gracefully."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    _add_test_document(client)

    webhook_response = client.post(
        "/webhook",
        data={
            "From": "whatsapp:+12345678901",
            # Body field is intentionally missing
        },
    )

    assert webhook_response.status_code == 200
    payload = webhook_response.json()
    assert "matched_document_id" not in payload or payload.get("matched_document_id") is None  # Empty query won't match


def test_webhook_with_invalid_signature_when_required(tmp_path: Path) -> None:
    """Test webhook rejects invalid Twilio signature when validation is enabled."""
    _configure_test_state(tmp_path)
    
    # Temporarily enable signature validation
    with patch.object(settings, "require_twilio_signature", True):
        with patch.object(settings, "twilio_auth_token", "test-auth-token"):
            client = TestClient(main_module.app)

            webhook_response = client.post(
                "/webhook",
                data={
                    "From": "whatsapp:+12345678901",
                    "Body": "send my resume",
                },
                headers={
                    "X-Twilio-Signature": "invalid-signature-xyz",
                },
            )

            # Should return 403 Forbidden for invalid signature
            assert webhook_response.status_code == 403
            assert "Invalid Twilio signature" in webhook_response.json()["detail"]


def test_webhook_with_unauthorized_sender(tmp_path: Path) -> None:
    """Test webhook rejects message from unauthorized sender when list is configured."""
    _configure_test_state(tmp_path)

    # Temporarily set an authorized senders list
    with patch.object(settings, "authorized_senders", "whatsapp:+19999999999"):
        client = TestClient(main_module.app)

        _add_test_document(client)

        # Try to send from unauthorized number
        webhook_response = client.post(
            "/webhook",
            data={
                "From": "whatsapp:+12345678901",  # Not in authorized list
                "Body": "send my resume",
            },
        )

        assert webhook_response.status_code == 200
        payload = webhook_response.json()
        assert "Unauthorized" in payload["message"]


def test_webhook_logs_request(tmp_path: Path) -> None:
    """Test webhook properly logs requests to the request log."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    doc_id = _add_test_document(client)

    webhook_response = client.post(
        "/webhook",
        data={
            "From": "whatsapp:+12345678901",
            "Body": "resume",
        },
    )

    assert webhook_response.status_code == 200

    # Check request logs
    logs_response = client.get("/logs/recent", params={"limit": 10})
    assert logs_response.status_code == 200
    logs = logs_response.json()

    # Should have at least the webhook log entry
    webhook_logs = [log for log in logs if log.get("type") == "webhook"]
    assert len(webhook_logs) > 0

    latest_webhook_log = webhook_logs[-1]
    assert latest_webhook_log["sender"] == "whatsapp:+12345678901"
    assert latest_webhook_log["query"] == "resume"
    assert latest_webhook_log["found"] is True
    assert latest_webhook_log["doc_id"] == doc_id


def test_webhook_response_includes_request_id_header(tmp_path: Path) -> None:
    """Test webhook response includes X-Request-ID header."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    webhook_response = client.post(
        "/webhook",
        data={
            "From": "whatsapp:+12345678901",
            "Body": "test query",
        },
    )

    assert webhook_response.status_code == 200
    assert "X-Request-ID" in webhook_response.headers
    assert webhook_response.headers["X-Request-ID"]  # Should not be empty
