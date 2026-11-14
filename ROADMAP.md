# InstaRetriv AI - Roadmap

**Current version: v3.0 (production hardening)**

## Product Goal

Build a WhatsApp-first personal document vault that stores, understands, and retrieves files quickly,
reliably, and securely.

## Scope

- Single-user vault (one dashboard owner, an allowlist of WhatsApp numbers)
- WhatsApp-based retrieval through Twilio
- Upload from a private web dashboard

## Architecture

- Backend: FastAPI + Uvicorn on Render
- Messaging: Twilio WhatsApp (sandbox today)
- Metadata and logs: MongoDB Atlas (JSON files for local development)
- File storage: Cloudinary private (`authenticated`) assets, streamed through `/files` (local disk for
  development)
- Matching: keywords, synonyms and RapidFuzz fuzzy matching

---

## Shipped

| Phase | Status | Summary |
|-------|--------|---------|
| 0 - Planning and setup | Done | Repo, environment, dependencies, environment template |
| 1 - Data and storage layer | Done | Metadata schema, JSON and MongoDB repositories, local and Cloudinary storage |
| 2 - Upload pipeline | Done | `POST /upload` with type validation, category, tags |
| 3 - WhatsApp webhook | Done | `POST /webhook`, signature validation, sender allowlist |
| 4 - Retrieval logic | Done | Keywords, synonyms, fuzzy matching, best-match selection |
| 5 - WhatsApp delivery | Done | Media sending, retries, link fallback |
| 6 - Logging and reliability | Done | Request IDs, health and setup status, structured logs |
| 6.5 - Document lifecycle | Done | Listing and soft archive |
| 7 - Testing | Done | Pytest suite (hermetic, no network) |
| 8 - Deployment | Done | Render service and Twilio webhook |
| 9 - MongoDB metadata | Done | Atlas backend with availability reporting |
| 10 - Cloudinary storage | Done | Durable storage that survives redeploys |
| 11 - Mixed old/new files | Done | Missing local files skipped, not served |
| 12 - Delivery recovery | Done | Link fallback only when media delivery fails |
| 12.5 - Cold start / duplicates | Done | MessageSid deduplication; first fresh message processed |
| 13 - Smart upload intelligence | Done | Filename tags, category suggestion (explicit categories such as `id` kept) |
| 14 - Secure vault mode | Done | Private documents with short-lived WhatsApp YES/1 confirmation and audit log (passcodes removed) |
| 15 - Delivery observability | Done | `POST /webhook/status`, `GET /logs/delivery`, `GET /logs/delivery/summary` |
| 16 - Admin dashboard | Done (v3.0) | Login, status panel with warnings, upload, documents, test query, recent activity |

### v3.0 - Production hardening (shipped)

- **Access control:**
  - Dashboard login is on by default, with no default password.
  - Failed logins are rate-limited per IP, with lockout.
  - Sessions are signed with `APP_SECRET_KEY`.
  - Every non-public route needs login.
- **Private files:**
  - `/files/{id}` needs a dashboard session or a short-lived HMAC-signed link.
  - Cloudinary uploads are `authenticated` and streamed by the app.
  - Archived documents are never served.
- **Webhook security:**
  - Twilio signatures are always enforced in production, and checked before deduplication.
  - The check is proxy-aware (`PUBLIC_BASE_URL`, `X-Forwarded-*`).
- **Confirmation flow:**
  - A bare keyword is never run as a search.
  - A new query supersedes a pending confirmation.
  - Expired confirmations are reported clearly.
  - The prompt shows the configured keywords and the enforced time.
- **Matching quality:**
  - Filler words and 1-character tokens are ignored.
  - Fuzzy matching is safer, using `ratio` on words of 4+ letters.
  - Filename words are searchable.
  - Unicode text is supported.
- **Reliability:**
  - No silent fallback to temporary disk in production. `/health` reports `degraded` and `/ready` returns 503.
  - MongoDB connections are retried.
  - Atomic JSON writes, and corrupt files are quarantined.
  - Blocking work runs off the event loop.
  - Transient errors never archive documents.
  - Twilio sends are retried only for transient errors.
- **Privacy:** Numbers are masked and message text is cleaned in logs.
- **Operations:**
  - Pinned runtime dependencies, with dev dependencies split out.
  - Python 3.12.8 pinned.
  - Proxy headers on Render.
  - Auto-reload only in development.
  - A complete `.env.example`.
  - The ngrok binary is no longer tracked in git.

---

## Next Steps

### 1. WhatsApp Business API sender
- Move from the Twilio sandbox to an approved WhatsApp Business sender (display name "InstaRetriv AI",
  profile photo). This removes the 72-hour sandbox re-join and the `join <code>` step.
- Add approved message templates for replies outside the 24-hour session window.

### 2. Multi-user support
- Per-user vaults (documents, allowlisted numbers, settings) with isolated storage prefixes.
- User accounts for the dashboard, with optional TOTP two-factor login.

### 3. Backups and data safety
- Scheduled MongoDB exports (Atlas backups on a paid tier, or a nightly `mongodump` job).
- A Cloudinary-to-object-storage backup or export tool, and a tool to restore metadata from storage.
- An "orphan check" that lists metadata without files and files without metadata.

### 4. Monitoring and alerting
- Uptime monitoring on `/ready`, with alerts by email or WhatsApp.
- Error tracking (for example Sentry) and structured JSON logs shipped to a log service.
- Alerts on repeated delivery failures from `/logs/delivery/summary`.

### 5. Retrieval and usability
- OCR and semantic search over document contents.
- Multiple matches: reply with a numbered list ("1. Resume 2024, 2. Resume 2022").
- Tag and category editing in the dashboard, and restoring archived documents.
- Usage analytics (top queries, misses), to improve synonyms and tags.

### 6. Platform
- A paid Render instance (no cold starts) or keep-warm pings.
- CI (GitHub Actions) running `pytest` on every push, plus dependency update checks.
