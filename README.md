# InstaRetriv AI

InstaRetriv AI is a personal WhatsApp-based document retrieval assistant.
Ask naturally and get your important file in seconds.

## Try These Prompts

- Send my resume
- Give CV
- I need my Aadhaar
- Send latest certificate

## How It Works

1. You send a WhatsApp message.
2. The FastAPI webhook receives your request.
3. The matcher checks tags and keywords.
4. The matched file is fetched from storage.
5. The document is sent back to WhatsApp.

## MVP Features

- Single-user personal assistant flow
- Twilio WhatsApp Sandbox integration
- Document upload with manual tags
- Keyword-based retrieval (fuzzy matching upgrade-ready)
- Request and response logging for debugging
- Request ID tracking on every API response (`X-Request-ID`)
- Local file serving endpoint for Twilio media fetch flow
- Optional Twilio signature verification for webhook security
- Global error handling with request-level trace IDs
- Document list and archive management endpoints
- Twilio send retry and graceful fallback messaging on delivery failure

## Planned Tech Stack

- Backend: FastAPI (Python)
- WhatsApp API: Twilio WhatsApp Sandbox
- Database: MongoDB or Firebase Firestore
- Storage: Local storage for MVP, AWS S3/Firebase Storage later
- NLP: Simple keyword matching

## API Plan

- POST /upload
	- Upload document with tags and category
- POST /webhook
	- Receive WhatsApp messages and trigger retrieval
- GET /get-document
	- Match request to stored document metadata
- GET /files/{document_id}
	- Serve stored file for document delivery
- GET /logs/recent
	- View recent request logs for debugging
- GET /documents
	- List documents (active only by default)
- DELETE /documents/{document_id}
	- Archive a document (soft deactivate)
- GET /setup/status
	- Check Twilio and public URL setup readiness (no secrets exposed)

## Quick Project Status

- Roadmap is defined.
- MVP backend foundation is implemented.
- Upload, retrieval, webhook, and logging endpoints are live.
- Twilio Sandbox end-to-end flow is validated with live WhatsApp document delivery.
- Current focus: security hardening and deployment readiness.

## Project Vision

Reduce friction in accessing important personal documents through simple natural language chat over WhatsApp.

## Why This Can Change The World

Small tools can create massive impact when they remove everyday friction.

- Faster access in urgent moments: people can instantly retrieve identity, education, or medical documents when timing matters.
- Better inclusion: WhatsApp is already familiar to millions, so users do not need to learn a new app.
- Lower digital barriers: natural language requests are easier than searching folders or navigating complex portals.
- A practical foundation: this personal assistant model can evolve into family, school, clinic, and community document helpers.

The bigger idea is simple: when essential information is easier to access, people can act faster, miss fewer opportunities, and solve real problems with less stress.

## Coming Soon

- Persistent cloud storage support (AWS S3 or Firebase Storage)
- MongoDB or Firestore integration for production metadata
- Better fuzzy matching and ranking controls
- Production deployment (Render/Railway/VPS) with stable public URL

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies from requirements.txt.
3. Open .env and fill values when ready (template is pre-created).
4. Run the app with: python run.py
5. Open docs at: http://127.0.0.1:8000/docs

### Dev Helper Commands (PowerShell)

- Start app: .\scripts\start_dev.ps1
- Start app with reload: .\scripts\start_dev.ps1 -Reload
- Check setup and API health: .\scripts\check_setup.ps1

### First API Checks

- GET /health
- GET /setup/status
- POST /upload with form fields: file, doc_category, tags
- GET /get-document?query=send my resume
- POST /webhook with Twilio form fields Body and From
- GET /logs/recent?limit=20
- GET /documents?active_only=true

### Twilio Media Sending Notes

- Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` in `.env`.
- Optionally tune `TWILIO_SEND_RETRIES` in `.env` (default: 2).
- Set `PUBLIC_BASE_URL` to your public server URL (example: ngrok URL).
- Set `REQUIRE_TWILIO_SIGNATURE=true` in production to validate webhook authenticity.
- Webhook will send media when both Twilio credentials and `PUBLIC_BASE_URL` are configured.

### Twilio Go-Live Checklist

1. Start API: .\scripts\start_dev.ps1
2. Start ngrok on port 8000 and copy HTTPS URL.
3. Set `PUBLIC_BASE_URL` in `.env` with ngrok URL.
4. In Twilio sandbox, set incoming webhook URL to `<PUBLIC_BASE_URL>/webhook`.
5. Send join code from your WhatsApp to Twilio sandbox number.
6. Upload one test document via `/upload`.
7. Send WhatsApp message: "Send my resume".
8. Inspect `/logs/recent` if anything fails.

### Latest Milestone (Completed)

- ngrok tunnel configured and working.
- Twilio Sandbox webhook connected to `/webhook`.
- WhatsApp participant joined sandbox successfully.
- End-to-end test passed: "send my resume" returned the uploaded PDF.
- Integration and validation test suite remains green (26 passing tests).

### Implemented in This Iteration

- Upload validation for allowed file extensions.
- Persistent request log file for upload, retrieval, and webhook events.
- Request ID middleware for traceability.
- Outbound Twilio text/media sending service.
- File serving endpoint used for WhatsApp media delivery.
- Twilio signature validation helper and optional webhook verification toggle.
- Global middleware handling for unexpected errors.
- Document listing and archive endpoints.
- Basic pytest coverage for matcher and repository flows.
- API integration tests for setup, upload/retrieve, and archive flows.

### Run Tests

1. Install test dependency from requirements-dev.txt.
2. Run: python -m pytest -q
3. Current coverage: 26 tests across core functionality, webhook endpoints, and error handling.

#### Test Categories

**Core Functionality Tests (3 tests)**
- Setup status endpoint validation
- Upload → retrieval flow with document matching
- Document listing and archive lifecycle

**Webhook Endpoint Tests (9 tests)**
- Document match and response generation
- No document found scenarios
- Missing required fields (From, Body) handling
- Invalid Twilio signature rejection (when enabled)
- Unauthorized sender filtering
- Request logging on webhook events
- Response header validation (X-Request-ID)

**Error Handling & Validation Tests (12 tests)**
- Invalid file uploads (missing name, unsupported extensions)
- Missing or invalid required fields (tags, queries)
- API constraint validation (log limits, document IDs)
- Nonexistent resource handling (404 responses)
- Request ID header presence on all responses
- Health endpoint status validation

**Unit Tests (2 tests)**
- Document matcher with synonyms
- Tie-break scoring by recency

All tests run in isolation with temporary storage (tmp_path fixture) to ensure clean state.