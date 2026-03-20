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

## Quick Project Status

- Roadmap is defined.
- MVP backend foundation is implemented.
- Upload, retrieval, webhook, and logging endpoints are live.
- Current focus: Twilio end-to-end media delivery testing via public URL.

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
- End-to-end live demo on Twilio Sandbox

## Quick Start

1. Create and activate a virtual environment.
2. Install dependencies from requirements.txt.
3. Copy .env.example to .env and fill values if needed.
4. Run the app with: python run.py
5. Open docs at: http://127.0.0.1:8000/docs

### First API Checks

- GET /health
- POST /upload with form fields: file, doc_category, tags
- GET /get-document?query=send my resume
- POST /webhook with Twilio form fields Body and From
- GET /logs/recent?limit=20

### Twilio Media Sending Notes

- Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` in `.env`.
- Set `PUBLIC_BASE_URL` to your public server URL (example: ngrok URL).
- Webhook will send media when both Twilio credentials and `PUBLIC_BASE_URL` are configured.

### Implemented in This Iteration

- Upload validation for allowed file extensions.
- Persistent request log file for upload, retrieval, and webhook events.
- Request ID middleware for traceability.
- Outbound Twilio text/media sending service.
- File serving endpoint used for WhatsApp media delivery.