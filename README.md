# InstaRetriv AI

InstaRetriv AI is a WhatsApp-first document retrieval system that lets you upload files once and fetch them instantly using natural language messages.

Example query:
Send my Aadhaar card

Result:
The system finds the best matching document and delivers it directly through WhatsApp.

## Project Status

Production deployment is live on Render with:
- FastAPI backend running continuously
- Twilio webhook integration active
- MongoDB Atlas metadata backend connected
- End-to-end retrieval and media delivery verified

## Why InstaRetriv AI

- WhatsApp-native retrieval experience
- Fast and practical for personal document workflows
- Strongly typed backend with clean service boundaries
- Secure webhook validation and sender restrictions
- Designed for easy evolution into AI-powered retrieval

## Key Features

- Upload document files with category and tags
- Retrieval by natural language text queries
- Matching engine with keywords, synonyms, and fuzzy scoring
- Twilio inbound webhook processing
- Outbound WhatsApp text and media with retry logic
- Document serving endpoint for Twilio media fetch
- Soft-archive document lifecycle
- Structured request tracing with request IDs
- Persistent request logs for debugging

## Architecture

- API Layer: FastAPI
- Messaging Layer: Twilio WhatsApp Sandbox
- Metadata Layer: MongoDB Atlas (with JSON fallback strategy)
- Storage Layer: Local file storage (current)
- Matching Layer: Token + synonym + fuzzy ranking
- Observability: Request logs + health and setup status endpoints

## Tech Stack

- Python
- FastAPI
- Uvicorn
- Pydantic Settings
- Twilio SDK
- PyMongo
- RapidFuzz
- Pytest

## API Surface

- GET /
- GET /health
- GET /setup/status
- POST /upload
- GET /get-document
- POST /webhook
- GET /files/{document_id}
- GET /documents
- DELETE /documents/{document_id}
- GET /logs/recent
- GET /admin/documents
- GET /admin/search

## Local Setup

1. Create and activate virtual environment.
2. Install runtime dependencies.
3. Create .env from .env.example.
4. Start server.
5. Open API docs.

Commands:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

API docs:

```text
http://127.0.0.1:8000/docs
```

## Environment Configuration

Core keys:
- APP_ENV
- HOST
- PORT
- AUTHORIZED_SENDERS
- PUBLIC_BASE_URL

Twilio keys:
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_SECONDARY_AUTH_TOKEN (optional)
- TWILIO_WHATSAPP_FROM
- REQUIRE_TWILIO_SIGNATURE

Metadata backend keys:
- METADATA_BACKEND (json or mongo)
- MONGODB_URI
- MONGODB_DATABASE
- MONGODB_COLLECTION

Storage backend keys:
- STORAGE_BACKEND (local or cloudinary)
- CLOUDINARY_CLOUD_NAME
- CLOUDINARY_API_KEY
- CLOUDINARY_API_SECRET

## MongoDB Setup

1. Create MongoDB Atlas cluster.
2. Create DB user credentials.
3. Allow network access from your deployment environment.
4. Set MONGODB_URI in environment variables.
5. Set METADATA_BACKEND=mongo.
6. Verify setup using GET /setup/status.

Success indicator:
- mongodb_uri_set = true
- mongo_backend_selected = true

## Render Deployment

This repository includes deployment files:
- render.yaml
- Procfile
- runtime.txt

Deploy flow:
1. Push code to GitHub.
2. Create Render web service from the repository.
3. Apply required environment variables in Render.
4. Confirm service health on /health.
5. Confirm readiness on /setup/status.
6. Point Twilio Sandbox webhook to:

```text
https://<your-render-domain>/webhook
```

Note:
- Set PUBLIC_BASE_URL to base domain only, not /webhook.

## Cloudinary Durable Storage Setup

Use this to keep uploaded files available even after Render restarts/redeploys.

1. Create a Cloudinary account.
2. Copy these values from Cloudinary dashboard:
- CLOUDINARY_CLOUD_NAME
- CLOUDINARY_API_KEY
- CLOUDINARY_API_SECRET
3. In Render environment variables set:
- STORAGE_BACKEND=cloudinary
- CLOUDINARY_CLOUD_NAME=<value>
- CLOUDINARY_API_KEY=<value>
- CLOUDINARY_API_SECRET=<value>
4. Redeploy service.
5. Upload a new document and test retrieval from WhatsApp.

Free tier:
- Yes, Cloudinary free plan is enough to start MVP/testing.

## Testing

Run automated tests:

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

## Operational Notes

- Keep Twilio webhook URL and PUBLIC_BASE_URL aligned.
- Rotate Twilio and MongoDB secrets after public sharing.
- Sandbox behavior can vary; always confirm active participant status.
- If STORAGE_BACKEND=local, files can disappear after redeploy.
- If STORAGE_BACKEND=cloudinary, files remain durable across redeploys.

## Completed Milestones

- Initial MVP pipeline complete
- Twilio signature validation hardened for public URL deployment
- MongoDB backend integrated with compatibility fallback
- Render deployment completed
- End-to-end WhatsApp media delivery validated in production

## Coming Soon

- Intelligence Layer: auto-tagging engine that understands document context before storage
- Secure Vault Mode: extra verification path for personal-sensitive files
- Retrieval Brain v2: intent-aware ranking that adapts to your query style over time
- Control Hub: compact command center for analytics, document health, and smart actions