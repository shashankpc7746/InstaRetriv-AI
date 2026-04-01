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

## MongoDB Setup (New)

1. Install runtime dependencies: pip install -r requirements.txt.
2. Run MongoDB locally (or use MongoDB Atlas).
3. Configure .env values:
- METADATA_BACKEND=mongo
- MONGODB_URI=<your_mongodb_connection_string>
- MONGODB_DATABASE=instaretriv_ai
- MONGODB_COLLECTION=documents
4. Restart app: .\scripts\start_dev.ps1.
5. Verify backend selection from setup endpoint:
- GET /setup/status
- Confirm mongo_backend_selected is true.

## Deploy to Render

1. Push latest code to GitHub.
2. In Render dashboard, click New + -> Blueprint.
3. Select this repository and deploy using render.yaml.
4. In Render service environment variables, set:
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_SECONDARY_AUTH_TOKEN (optional)
- TWILIO_WHATSAPP_FROM
- PUBLIC_BASE_URL (your Render app URL)
- AUTHORIZED_SENDERS
- MONGODB_URI
5. After deploy is live, open /setup/status and verify:
- mongo_backend_selected = true
- public_base_url_set = true

Twilio final step:
- Set Twilio Sandbox webhook to https://<your-render-domain>/webhook
- Keep method POST

## Daily Startup Checklist

1. Start API: .\scripts\start_dev.ps1
2. Start ngrok: ngrok http 8000
3. Update PUBLIC_BASE_URL if ngrok URL changes.
4. Update Twilio Sandbox webhook URL to <PUBLIC_BASE_URL>/webhook if URL changed.
5. Send WhatsApp test message: send my resume.

## Upcoming Work

- MongoDB metadata backend with feature toggle (JSON and Mongo modes).
- Data migration utility from local metadata JSON to MongoDB.
- LLM-based auto-tagging and document type classification.
- Passcode protection flow for sensitive documents on WhatsApp retrieval.
- Production environment hardening and secrets management.

## Future Enhancements

- Cloud file storage (S3/Firebase Storage).
- Better ranking controls and retrieval analytics.
- Multi-user access model.
- Role-based authentication and admin controls.
- AI-assisted tagging and semantic retrieval.
- Dashboard for upload/search/log monitoring.
- Version history and document revisions.