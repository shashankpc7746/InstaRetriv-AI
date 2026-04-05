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
from app.services.whatsapp import WhatsAppSender


def _configure_test_state(tmp_path: Path) -> None:
    """Configure temporary storage for test isolation."""
    upload_dir = tmp_path / "uploads"
    metadata_file = tmp_path / "metadata.json"
    request_log_file = tmp_path / "request_logs.json"

    main_module.repository = MetadataRepository(str(metadata_file))
    main_module.request_logs = RequestLogRepository(str(request_log_file))
    main_module.storage_service = LocalStorageService(str(upload_dir))
    main_module.whatsapp_sender = WhatsAppSender(account_sid="", auth_token="", sender="")
    main_module.settings.require_twilio_signature = False
    main_module.settings.authorized_senders = ""
    main_module.settings.public_base_url = ""


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


def test_status_callback_logs_request(tmp_path: Path) -> None:
    """Test Twilio status callback endpoint stores delivery status logs."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    callback_response = client.post(
        "/webhook/status",
        data={
            "MessageSid": "SM111",
            "MessageStatus": "delivered",
            "To": "whatsapp:+12345678901",
            "From": "whatsapp:+14155238886",
        },
    )

    assert callback_response.status_code == 200
    assert callback_response.json()["message"] == "Status callback received"

    logs_response = client.get("/logs/recent", params={"limit": 20})
    assert logs_response.status_code == 200
    logs = logs_response.json()
    callback_logs = [entry for entry in logs if entry.get("type") == "twilio-status-callback"]

    assert len(callback_logs) == 1
    assert callback_logs[0]["twilio_sid"] == "SM111"
    assert callback_logs[0]["message_status"] == "delivered"
    assert callback_logs[0]["normalized_state"] == "delivered"


def test_delivery_logs_correlate_webhook_and_status_callbacks(tmp_path: Path) -> None:
    """Test delivery logs endpoint correlates webhook sends with callback statuses."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    main_module.request_logs.add(
        {
            "request_id": "req-1",
            "type": "webhook",
            "sender": "whatsapp:+12345678901",
            "query": "resume",
            "found": True,
            "doc_id": "doc-1",
            "twilio_sid": "SMABC123",
        }
    )
    main_module.request_logs.add(
        {
            "request_id": "req-2",
            "type": "twilio-status-callback",
            "twilio_sid": "SMABC123",
            "message_status": "read",
            "normalized_state": "read",
            "error_code": None,
            "error_message": None,
        }
    )

    delivery_response = client.get("/logs/delivery", params={"limit": 10})
    assert delivery_response.status_code == 200
    payload = delivery_response.json()

    assert len(payload) == 1
    assert payload[0]["twilio_sid"] == "SMABC123"
    assert payload[0]["delivery_status"] == "read"
    assert payload[0]["normalized_delivery_state"] == "read"
    assert payload[0]["query"] == "resume"


def test_delivery_summary_reports_success_rate(tmp_path: Path) -> None:
    """Test delivery summary endpoint computes per-state counts and success rate."""
    _configure_test_state(tmp_path)
    client = TestClient(main_module.app)

    main_module.request_logs.add(
        {
            "request_id": "req-1",
            "type": "twilio-status-callback",
            "twilio_sid": "SM1",
            "message_status": "delivered",
            "normalized_state": "delivered",
            "error_code": None,
            "error_message": None,
        }
    )
    main_module.request_logs.add(
        {
            "request_id": "req-2",
            "type": "twilio-status-callback",
            "twilio_sid": "SM2",
            "message_status": "undelivered",
            "normalized_state": "failed",
            "error_code": "30003",
            "error_message": "Unreachable destination handset",
        }
    )
    main_module.request_logs.add(
        {
            "request_id": "req-3",
            "type": "twilio-status-callback",
            "twilio_sid": "SM3",
            "message_status": "queued",
            "normalized_state": "queued",
            "error_code": None,
            "error_message": None,
        }
    )

    summary_response = client.get("/logs/delivery/summary", params={"limit": 100})
    assert summary_response.status_code == 200

    summary = summary_response.json()
    assert summary["tracked_message_count"] == 3
    assert summary["terminal_message_count"] == 2
    assert summary["successful_terminal_count"] == 1
    assert summary["success_rate_percent"] == 50.0
    assert summary["counts_by_state"]["delivered"] == 1
    assert summary["counts_by_state"]["failed"] == 1
    assert summary["counts_by_state"]["queued"] == 1
