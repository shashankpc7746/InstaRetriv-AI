# InstaRetriv AI

InstaRetriv AI is a production-ready WhatsApp document assistant that stores your files, understands natural-language queries, and returns the right document through Twilio with minimal friction.

Example:
Send my resume

Result:
The assistant finds the best match and delivers the file to WhatsApp, with durable cloud storage and fallback behavior for edge cases.

## Version

v2.0

What v2.0 achieved after v1.0:
- Production deployment on Render
- MongoDB Atlas metadata persistence
- Cloudinary-backed durable file storage
- Safer retrieval flow with stale-file skipping
- WhatsApp delivery fallback handling

## Project Status

The system is live and operational with:
- FastAPI backend deployed on Render
- Twilio WhatsApp Sandbox integration
- MongoDB Atlas for document metadata
- Cloudinary for durable file delivery
- Automated health and setup checks

## Why This Project Matters

- It turns WhatsApp into a practical personal document vault.
- It removes the need to manually search folders and chat histories.
- It is built for real usage, not just a demo flow.
- It already handles the painful parts: webhooks, retries, storage durability, and stale-data cleanup.

## What It Can Do

- Upload files with categories and tags
- Retrieve documents using conversational queries
- Match by keywords, synonyms, and fuzzy scoring
- Deliver files through WhatsApp
- Fall back to a direct file link when media delivery fails
- Store metadata in MongoDB
- Store files in Cloudinary so they survive redeploys
- Auto-skip stale file records and keep retrieval moving
- Log requests with trace IDs for debugging

## Architecture Overview

- API: FastAPI
- Messaging: Twilio WhatsApp Sandbox
- Metadata: MongoDB Atlas
- File storage: Cloudinary
- Retrieval: keyword + synonym + fuzzy matching
- Observability: request logs, health checks, setup status endpoint

## Tech Stack

- Python
- FastAPI
- Uvicorn
- Twilio SDK
- PyMongo
- Cloudinary SDK
- RapidFuzz
- Pydantic Settings
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

1. Create and activate a virtual environment.
2. Install runtime dependencies.
3. Configure [.env](.env) from [.env.example](.env.example).
4. Start the app locally.
5. Open the docs UI.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Docs:

```text
http://127.0.0.1:8000/docs
```

## Required Environment Variables

Application:
- APP_ENV
- HOST
- PORT
- AUTHORIZED_SENDERS
- PUBLIC_BASE_URL

Twilio:
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_SECONDARY_AUTH_TOKEN
- TWILIO_WHATSAPP_FROM
- REQUIRE_TWILIO_SIGNATURE

Metadata:
- METADATA_BACKEND
- MONGODB_URI
- MONGODB_DATABASE
- MONGODB_COLLECTION

Storage:
- STORAGE_BACKEND
- CLOUDINARY_CLOUD_NAME
- CLOUDINARY_API_KEY
- CLOUDINARY_API_SECRET

## Deployment

This repo includes Render deployment files:
- render.yaml
- Procfile
- runtime.txt

Deploy steps:
1. Push the latest code to GitHub.
2. Create or update the Render web service.
3. Set environment variables in Render.
4. Redeploy the service.
5. Confirm `/health` and `/setup/status` both work.
6. Point Twilio Sandbox webhook to the Render URL.

Webhook URL:

```text
https://<your-render-domain>/webhook
```

Important:
- Use the base domain in `PUBLIC_BASE_URL`.
- Keep secret values out of git; enter them directly in Render.

## MongoDB Setup

MongoDB is used for metadata only.

Steps:
1. Create the MongoDB Atlas cluster.
2. Create a database user.
3. Allow network access.
4. Set `METADATA_BACKEND=mongo`.
5. Paste `MONGODB_URI`.
6. Verify `/setup/status` shows Mongo enabled.

Expected status:

```json
{
  "mongodb_uri_set": true,
  "mongo_backend_selected": true
}
```

## Cloudinary Storage Setup

Cloudinary is used for durable file storage.

Steps:
1. Create a Cloudinary account.
2. Copy cloud name, API key, and API secret.
3. Set `STORAGE_BACKEND=cloudinary` in Render.
4. Save the Cloudinary values in Render environment variables.
5. Redeploy.
6. Upload a fresh file and test retrieval.

Benefits:
- Files survive Render redeploys
- Twilio can fetch stable public URLs
- No dependency on local disk persistence

## Testing

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

## Operational Notes

- If a file was uploaded before Cloudinary was enabled, re-upload it.
- If a stale local file is matched, the app now skips it and continues.
- The system sends a direct link only when WhatsApp media delivery fails.
- Keep secrets rotated after validation.

## What v2.0 Solves

- Production hosting with Render
- Durable file storage with Cloudinary
- Metadata persistence with MongoDB
- Broken local-disk file lookups after redeploys
- Better recovery when older documents are stale
- Safer WhatsApp delivery with link fallback only on failure

## Coming Soon

- Intelligence Layer: upload-time document understanding without manual tag work
- Secure Vault Mode: extra verification for sensitive files
- Retrieval Brain v2: smarter ranking that learns your query style
- Control Hub: a compact command center for search, health, and document insights
