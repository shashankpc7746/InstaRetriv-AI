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
### Status
Partially completed

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

### Implemented so far
- Optional Twilio signature verification support via config toggle.

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
Completed (MVP)

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
- Added Twilio retry logic for outbound messages with graceful fallback responses.

---

## Phase 6 - Logging, Reliability, and Hardening
### Status
Completed (MVP)

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
- Added global middleware fallback for unhandled exceptions with request ID tracing.

---

## Phase 6.5 - Document Lifecycle Management
### Status
Completed (MVP utility)

### Tasks
- Add endpoint to list stored documents.
- Add endpoint to archive/deactivate a document without deleting metadata.

### Deliverables
- `GET /documents` endpoint for active/all view.
- `DELETE /documents/{document_id}` endpoint for soft archive.

---

## Phase 7 - Testing and Validation
### Status
Completed (MVP validation)

### Tasks
- Test known queries: "send my resume", "give cv", "need my aadhaar"
- Test unknown query behavior
- Test duplicate tag scenarios
- Test invalid file upload input

### Deliverables
- Test checklist and pass/fail report
- Confirmed MVP acceptance criteria

### Implemented so far
- Added pytest-based unit tests for matcher behavior.
- Added pytest-based tests for repository add/deactivate flow.
- Added API integration tests for setup status, upload/retrieve, and archive flows.
- Added webhook and error-handling integration tests.
- Total automated tests passing: 26.

---

## Phase 8 - Deployment (Prototype)
### Status
In progress

### Tasks
- Deploy FastAPI to a public host
- Set production webhook URL in Twilio sandbox
- Verify environment variables in deployment

### Deliverables
- Live prototype URL
- Stable WhatsApp retrieval demo

### Setup Preparation Completed
- Added pre-created `.env` template for local setup.
- Added helper scripts for start and setup checks.
- Added `GET /setup/status` endpoint to verify Twilio/public URL readiness.
- Live ngrok + Twilio sandbox end-to-end messaging validated.

---

## Phase 9 - MongoDB Metadata Backend
### Status
In progress

### Tasks
- Add MongoDB configuration support in app settings.
- Add MongoDB-based repository with the same CRUD interface.
- Add repository selection toggle (JSON vs Mongo).
- Keep local JSON fallback for reliability.
- Add/update tests to validate selected backend behavior.

### Deliverables
- Working metadata persistence with MongoDB.
- Backward-compatible local JSON mode.
- Clear setup instructions for Mongo local/cloud usage.

### Implemented so far
- Added MongoDB backend configuration keys in settings and .env template.
- Added repository toggle support (json vs mongo).
- Added safe fallback to JSON repository when Mongo is unavailable.

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
- LLM intent understanding (planned for Phase 10)
- Voice message support
- Auto-tagging with AI (planned for Phase 10)
- Passcode security for sensitive documents (planned for Phase 11)
- Version history UI/dashboard

---

## Phase 10 - LLM Auto-Tagging for Smart Classification
### Status
Not started (Planned for next session)

### Description
Integrate LLM (Claude API or open-source Ollama) to automatically analyze document content and generate relevant tags. This improves search accuracy and reduces manual tagging burden.

### Tasks
- Add LLM integration (Claude API or Ollama)
- Create document analyzer service
- On upload: analyze document content → extract document type
- Auto-generate tags based on content (e.g., "PAN Card" → tags: "pan, tax, identification, govt-issued")
- Update `/upload` endpoint to show auto-generated tags and allow user approval
- Store confidence score with each auto-generated tag
- Add manual override option

### Deliverables
- LLM-powered tagging service
- Enhanced `/upload` endpoint with auto-tags preview
- Test with various document types (resume, PAN, Aadhar, invoices, etc.)

### Benefits
- Faster document upload (no manual tagging)
- Better search results (comprehensive auto-tags)
- Automatic document classification
- Improved user experience

---

## Phase 11 - Passcode Security for Sensitive Documents
### Status
Not started (Planned for next session)

### Description
Add optional passcode protection for sensitive personal documents. User can mark documents as "secured" and set a passcode. On WhatsApp request, the system will ask for the passcode before delivering the file. This prevents unauthorized access even if someone gains access to the WhatsApp account.

### Tasks
- Add `is_secured` and `passcode_hash` fields to DocumentMetadata schema
- Create security service for passcode hashing and verification (bcrypt)
- Update `/upload` endpoint to accept optional `is_secured` flag and `passcode`
- Update `/webhook` to check if document is secured
- If secured: instead of sending file, respond with "This is a secured document. Please enter the passcode."
- Implement passcode verification logic (accept multiple attempts, add cooldown)
- Store attempt logs for security audit
- Add `/admin/security-logs` endpoint to view unauthorized access attempts
- Update `/documents` endpoint to show security status

### Deliverables
- Passcode-protected document delivery
- Security audit logs
- User-friendly WhatsApp interaction for secured documents
- Documentation on how to secure documents

### Benefits
- Protect sensitive personal documents (Passport, Bank Statements, etc.)
- Two-factor security (possession of WhatsApp + passcode knowledge)
- Audit trail for access attempts
- User choice on which documents to secure
- Peace of mind for personal data protection

### Example Workflow
1. User uploads Passport document → marks as "secured" with passcode "1234"
2. User requests: "send my passport" via WhatsApp
3. System responds: "This is a secured document. Please reply with the passcode."
4. User replies: "1234"
5. System verifies passcode → sends passport file
6. If wrong passcode: "Incorrect passcode. Please try again. (Attempt 1/3)"

---

## Next Step for Future Session
Complete Mongo CRUD validation on live Mongo instance, then deploy to stable host. After deployment, proceed with Phase 10 (LLM auto-tagging) for smarter document classification.