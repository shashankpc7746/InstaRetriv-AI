# InstaRetriv AI

InstaRetriv AI is a personal WhatsApp document retrieval assistant built with FastAPI and Twilio Sandbox.
Send a message like "send my resume" and receive the matched file directly on WhatsApp.

## Current Version

Checkpoint: v0

What is confirmed working end-to-end:
- Document upload via API.
- Query-based document retrieval.
- WhatsApp webhook processing.
- Twilio media send flow.
- Signature validation support.
- Request logging and trace IDs.

## Core Features

- Single-user WhatsApp assistant flow.
- Upload endpoint with extension and tag validation.
- Keyword, synonym, and fuzzy retrieval matching.
- Twilio outbound text/media send with retries.
- File serving endpoint for Twilio media fetch.
- Soft archive support for documents.
- Health and setup readiness endpoints.
- Structured request logging with X-Request-ID tracing.

## Tech Stack

- Backend: FastAPI
- WhatsApp API: Twilio Sandbox
- Metadata storage (current): local JSON
- File storage (current): local filesystem
- Matching: token + synonym + fuzzy scoring
- Tests: pytest + FastAPI TestClient

## API Endpoints

- GET /health
- GET /setup/status
- POST /upload
- GET /get-document
- POST /webhook
- GET /files/{document_id}
- GET /documents
- DELETE /documents/{document_id}
- GET /logs/recent

## Quick Start

1. Create and activate virtual environment.
2. Install dependencies from requirements files.
3. Configure .env from .env.example.
4. Start app: python run.py or .\scripts\start_dev.ps1.
5. Open API docs at http://127.0.0.1:8000/docs.

### Dev Helpers

- .\scripts\start_dev.ps1
- .\scripts\start_dev.ps1 -Reload
- .\scripts\check_setup.ps1

### Run Tests

1. Install test dependencies from requirements-dev.txt.
2. Run: python -m pytest -q
3. Current test status: 26 passing.

## Twilio Notes

- Keep PUBLIC_BASE_URL and Twilio webhook URL aligned.
- Keep webhook URL as HTTPS.
- If ngrok URL changes, update both .env and Twilio sandbox webhook config.
- For auth token rotation windows, optional secondary auth token is supported.

## Upcoming Work

- MongoDB metadata backend with feature toggle (JSON and Mongo modes).
- Data migration utility from local metadata JSON to MongoDB.
- Deployment to stable host (Render/Railway/VPS).
- Production environment hardening and secrets management.

## Future Enhancements

- Cloud file storage (S3/Firebase Storage).
- Better ranking controls and retrieval analytics.
- Multi-user access model.
- Role-based authentication and admin controls.
- AI-assisted tagging and semantic retrieval.
- Dashboard for upload/search/log monitoring.
- Version history and document revisions.