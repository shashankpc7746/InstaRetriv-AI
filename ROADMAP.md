# InstaRetriv AI - Roadmap

## Product Goal

Build a WhatsApp-first document assistant that can store, understand, and retrieve personal files quickly and reliably.

## Current Direction

The project has moved from local MVP into a real deployed system:
- Production deployment is live on Render
- Metadata is stored in MongoDB Atlas
- Files are stored in Cloudinary
- Twilio webhook flow is working end-to-end
- Retrieval logic now handles stale records more gracefully

## Scope

- Single-user workflow
- WhatsApp-based document access
- Manual upload with tags today
- Smarter upload and security features next

## Success Criteria

- Upload a document and store metadata successfully
- Send a WhatsApp query and get the correct file or link
- Survive redeploys without losing file delivery capability
- Keep logs, health checks, and status checks working

## Architecture

- Backend: FastAPI
- Messaging: Twilio WhatsApp Sandbox
- Metadata DB: MongoDB Atlas
- File storage: Cloudinary
- Matching: keyword, synonym, fuzzy scoring

---

## Phase 0 - Planning and Setup

### Status
Completed

### Achievements
- Repository structure created
- Python environment configured
- Runtime and dev dependencies established
- Environment template introduced

---

## Phase 1 - Core Data and Storage Layer

### Status
Completed

### Achievements
- Metadata schema created
- JSON repository implemented
- MongoDB repository added
- Local storage service introduced
- Cloudinary storage backend added later for durability

---

## Phase 2 - Upload Pipeline

### Status
Completed

### Achievements
- `POST /upload` implemented
- File type validation added
- Category and tags saved with each document
- Local and cloud storage support added

---

## Phase 3 - WhatsApp Webhook Integration

### Status
Completed

### Achievements
- `POST /webhook` implemented
- Twilio signature validation supported
- Sender restrictions added
- Webhook request logging added

---

## Phase 4 - Retrieval Logic

### Status
Completed

### Achievements
- Keyword matching implemented
- Synonym mapping added
- Fuzzy fallback added
- Best-match selection stabilized

---

## Phase 5 - Send Document via WhatsApp

### Status
Completed

### Achievements
- Outbound WhatsApp media sending added
- Retry logic added
- Public media URL generation added
- Direct link fallback added for delivery failures

---

## Phase 6 - Logging, Reliability, and Hardening

### Status
Completed

### Achievements
- Request ID middleware added
- Health endpoint added
- Setup status endpoint added
- Error logging improved
- Delivery tracing improved

---

## Phase 6.5 - Document Lifecycle Management

### Status
Completed

### Achievements
- Document listing endpoint added
- Archive/deactivate flow added
- Stale record cleanup behavior introduced

---

## Phase 7 - Testing and Validation

### Status
Completed

### Achievements
- Test suite created
- Webhook tests added
- Error handling tests added
- 26 automated tests previously verified as passing

---

## Phase 8 - Deployment

### Status
Completed

### Achievements
- Render deployment configured
- Production service live
- Twilio webhook connected
- Production health and setup checks verified

---

## Phase 9 - MongoDB Metadata Backend

### Status
Completed

### Achievements
- MongoDB integration wired into app settings
- Backend toggle added
- Fallback behavior preserved
- Live Atlas metadata storage verified

---

## Phase 10 - Cloudinary Durable File Storage

### Status
Completed

### Achievements
- Cloudinary file backend integrated
- Render file durability improved
- Remote media delivery supported
- PDF and document handling made more reliable

---

## Phase 11 - Retrieval Stability for Mixed Old/New Files

### Status
Completed

### Achievements
- Stale local-file matches auto-skipped
- Missing file records auto-archived
- Next-best retrievable document selected automatically
- Broken file references prevented from blocking retrieval

---

## Phase 12 - Delivery Recovery Improvements

### Status
Completed

### Achievements
- Link fallback used only when media delivery fails
- Successful media sends no longer trigger extra link messages
- Better user experience for WhatsApp delivery flows

---

## Phase 12.5 - Cold-Start and Duplicate Retry Reliability

### Status
Completed

### Achievements
- Replaced blanket cold-start blocking behavior
- Added MessageSid-based webhook deduplication
- Preserved first fresh user message processing after wake-up
- Prevented duplicate/retried webhook events from replaying stale commands

---

## Current Priorities

1. Add upload-time intelligence for better tags.
2. Add sensitive-document passcode protection.
3. Add delivery status tracking for Twilio callbacks.
4. Add cleanup tooling for stale records and old uploads.

---

## Next Planned Phases

## Phase 13 - Smart Upload Intelligence

### Goal
Reduce manual tag work and improve retrieval quality at upload time.

### Outcomes
- Better document classification
- Smarter tags from filenames and content hints
- Less user effort during upload

---

## Phase 14 - Secure Vault Mode

### Goal
Protect highly sensitive documents with extra verification.

### Outcomes
- Optional passcode for private documents
- Controlled access over WhatsApp
- Better protection for identity and financial files

---

## Phase 15 - Delivery Observability

### Goal
Track whether a WhatsApp delivery truly reached the user.

### Outcomes
- Delivery callback logging
- Better failure diagnostics
- Clearer message lifecycle state

---

## Phase 16 - Admin and Control Center

### Goal
Give a simple operational view of documents, delivery, and cleanup.

### Outcomes
- Document health view
- Cleanup actions
- Retrieval analytics

---

## Long-Term Direction

- Multi-user support
- Semantic retrieval
- Optional dashboard
- Smarter intent handling
- Cloud file migration utilities
