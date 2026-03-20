# InstaRetriv AI - MVP Roadmap

## Project Goal
Build a personal WhatsApp-based assistant that stores and retrieves documents using natural language text commands.

## Scope for MVP
- Single authorized user only
- WhatsApp integration via Twilio Sandbox
- Manual document upload with tags
- Keyword-based document matching (with optional fuzzy match)
- FastAPI backend with logging

## Success Criteria (Definition of Done)
- Upload a document with tags via API or script
- Send WhatsApp message like "Send my resume"
- Receive the correct document via WhatsApp
- If no match: return "Document not found. Please refine your request."
- All requests and responses are logged

## High-Level Architecture
- Backend: FastAPI
- WhatsApp API: Twilio WhatsApp Sandbox
- Metadata DB: MongoDB (or Firestore)
- File Storage: Local storage for MVP (upgradeable to S3/Firebase Storage)
- NLP: Keyword matching, optional fuzzy matching

---

## Phase 0 - Planning and Setup
### Tasks
- Finalize stack choices (MongoDB vs Firestore, local vs cloud storage)
- Create repository structure
- Configure Python environment and dependencies
- Set up `.env` for Twilio and DB credentials

### Deliverables
- Running FastAPI app scaffold
- Dependency file (`requirements.txt`)
- Environment configuration template (`.env.example`)

---

## Phase 1 - Core Data and Storage Layer
### Tasks
- Define metadata schema:
  - document name
  - type/category
  - tags
  - upload timestamp
  - storage path/url
- Implement DB connection module
- Implement local file storage service

### Deliverables
- Metadata model and CRUD helpers
- Storage utility functions (save, fetch path/url)

---

## Phase 2 - Upload Pipeline
### Status
Completed (MVP)

### Tasks
- Build `POST /upload` endpoint
- Accept file + tags + category
- Save file to storage
- Save metadata in database
- Add validation for unsupported files / missing tags

### Deliverables
- Working upload endpoint
- Example upload requests (curl/Postman)

---

## Phase 3 - WhatsApp Webhook Integration
### Status
Completed (MVP baseline)

### Tasks
- Build `POST /webhook` endpoint for Twilio inbound messages
- Parse incoming message text
- Normalize user text (lowercase, trim, punctuation cleanup)
- Return basic text response initially (before file sending)

### Deliverables
- Twilio Sandbox connected to webhook
- Incoming message successfully handled by FastAPI

---

## Phase 4 - Retrieval Logic
### Status
Completed (MVP baseline)

### Tasks
- Build internal retrieval service (`get_document` logic)
- Implement keyword-to-tag matching
- Add synonym mapping (example: cv -> resume)
- Add optional fuzzy matching threshold
- Handle tie-breaker by latest upload timestamp

### Deliverables
- Working retrieval logic used by webhook
- Predictable fallback when no document found

---

## Phase 5 - Send Document via WhatsApp
### Status
Partially completed

### Tasks
- On successful match, fetch file reference from storage
- Send document using Twilio WhatsApp media API
- Add error handling for send failures

### Deliverables
- End-to-end flow: user message -> document returned on WhatsApp

### Implemented so far
- Added outbound Twilio sender service (text and media).
- Added file serving endpoint (`GET /files/{document_id}`) for Twilio media URL fetch.
- Added `PUBLIC_BASE_URL` driven media URL generation in webhook flow.

---

## Phase 6 - Logging, Reliability, and Hardening
### Status
Partially completed

### Tasks
- Log inbound message, parsed query, match result, outbound response
- Add request IDs and timestamped logs
- Add health endpoint (`GET /health`)
- Add basic exception handling middleware

### Deliverables
- Structured logs for debugging
- Improved reliability for real usage

### Implemented so far
- Added request ID middleware (`X-Request-ID` response header).
- Added persistent JSON request logs and endpoint (`GET /logs/recent`).
- Added health endpoint.

---

## Phase 7 - Testing and Validation
### Tasks
- Test known queries: "send my resume", "give cv", "need my aadhaar"
- Test unknown query behavior
- Test duplicate tag scenarios
- Test invalid file upload input

### Deliverables
- Test checklist and pass/fail report
- Confirmed MVP acceptance criteria

---

## Phase 8 - Deployment (Prototype)
### Tasks
- Deploy FastAPI to a public host
- Set production webhook URL in Twilio sandbox
- Verify environment variables in deployment

### Deliverables
- Live prototype URL
- Stable WhatsApp retrieval demo

---

## Implementation Order (Strict)
1. Project scaffold + environment setup
2. Metadata model + DB and storage utilities
3. `/upload` endpoint
4. `/webhook` endpoint (text handling first)
5. Retrieval matcher (`get_document` logic)
6. Twilio media sending
7. Logging and hardening
8. Deployment and final validation

## Out of Scope for MVP (Future)
- Multi-user authentication
- LLM intent understanding
- Voice message support
- Auto-tagging with AI
- Version history UI/dashboard

## Next Step for Future Session
Complete Twilio end-to-end media delivery test with a public URL, then move metadata from local JSON to MongoDB/Firestore.