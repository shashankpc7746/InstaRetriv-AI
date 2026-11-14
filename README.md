# InstaRetriv AI

InstaRetriv AI is a personal document vault you use from WhatsApp. You upload files (resume, PAN card,
passport, certificates...) on a private web dashboard. Then you message the bot in plain language, for
example "send my resume", and it finds the best match and sends the file back on WhatsApp through Twilio.

```text
You:  send my pan card
Bot:  (PAN card PDF arrives as a WhatsApp attachment)
```

## Version

**v3.0: production hardening.**

What changed since v2.1:
- **Files are no longer public.** Every download goes through `GET /files/{id}`. It needs either a dashboard
  session or a short-lived signed link. New Cloudinary uploads are private (`authenticated`) and are streamed
  through the app.
- **The dashboard login is on by default** and there is no default password. It has rate limiting, lockout,
  and sessions signed with `APP_SECRET_KEY`.
- **Every non-public endpoint needs login**, including `/get-document`, `/documents`, `/upload`, `/logs/*`
  and `/setup/status`.
- **Twilio signatures are always checked in production.** A forged request is rejected before duplicate
  detection runs, so it cannot block the real message.
- **Fixes to the private-document confirmation:**
  - A bare "yes" with nothing pending never triggers a search.
  - A new question replaces a pending confirmation.
  - The prompt shows your configured keywords and the confirmation time that is actually enforced.
  - The leftover "private access code" feature has been removed.
- **Better matching:**
  - Filler words are ignored.
  - Tiny words such as "a", "i" and "ok" no longer match documents.
  - Typo tolerance is safer, and words from file names can be searched.
- **No silent fallbacks in production.** If MongoDB is unusable, `/health` shows `degraded`, `/ready`
  returns 503 and WhatsApp says the database is unreachable. If Cloudinary is unusable, `/health` shows
  `degraded` (`checks.storage`), uploads return 503 and the status panel warns; `/ready` stays 200 because
  existing documents can still be retrieved. Before, the app wrote to Render's temporary disk and answered
  "Document not found" for everything.
- **Safer delivery:**
  - Delivery problems never archive your documents.
  - Archived documents are never served.
  - Transient Twilio errors are retried, while permanent ones are not.
- **Safer storage and logging:**
  - JSON files are written atomically.
  - Uploads are size-limited.
  - Phone numbers and message text are masked in logs.
- **A new mobile-friendly dashboard** with a status panel, upload, document list, test search and recent activity.

## Features

- **Upload and organise:**
  - Upload PDF, images and Word files from the dashboard, with a category, tags and a private flag.
  - Tags are added automatically from the file name.
  - If you leave the category generic (for example `general` or `other`), one is suggested.
- **Natural-language retrieval.** The bot understands:
  - synonyms such as `cv` -> `resume`, `aadhar` -> `aadhaar` and `10th` -> `ssc`;
  - typos such as "pasport" and "certficate";
  - multi-word tags such as "pan card".

  Words like "send", "my" and "please" are ignored.
- **WhatsApp delivery:**
  - The file is sent as a WhatsApp attachment.
  - If the attachment fails, the bot sends a short-lived download link instead.
- **Private documents.** Sensitive files need a quick "YES" (or "1") reply on WhatsApp before they are sent.
- **Archive.** A soft delete from the dashboard. Archived files are never matched or served again.
- **Observability:**
  - Request IDs.
  - Request and audit logs, with masked numbers and text.
  - Twilio delivery-status tracking.
  - `/health`, `/ready` and a configuration status panel that lists every misconfiguration.

## Architecture

| Part | Development default | Production (Render) |
|------|---------------------|---------------------|
| API | FastAPI + Uvicorn | FastAPI + Uvicorn behind Render's proxy |
| Messaging | Twilio WhatsApp Sandbox (optional) | Twilio WhatsApp Sandbox or an approved sender |
| Metadata and logs | JSON files in `data/` | MongoDB Atlas |
| File storage | Local `uploads/` folder | Cloudinary (private `authenticated` assets) |
| Matching | Keywords, synonyms and fuzzy matching (RapidFuzz) | same |

```text
WhatsApp -> Twilio -> POST /webhook -> signature check -> sender allowlist -> duplicate check
         -> (private? ask YES/1) -> match document -> Twilio REST: send media with a signed /files link
Twilio  -> GET /files/{id}?exp=..&sig=.. -> app streams the file from Cloudinary/local disk
```

## Security model

1. **Dashboard login.**
   - `DASHBOARD_AUTH_ENABLED=true` by default.
   - Login stays disabled until you set `DASHBOARD_PASSWORD`. There is no built-in default password.
   - Failed logins are rate-limited per IP (the address appended by the hosting proxy, so a spoofed
     `X-Forwarded-For` value does not help). After `LOGIN_MAX_ATTEMPTS` failures, that IP is locked for
     `LOGIN_LOCKOUT_MINUTES`. An account-wide limit (4x `LOGIN_MAX_ATTEMPTS`, at least 20 failures from any
     addresses) also locks sign-in for that period, so rotating addresses cannot buy unlimited guesses.
   - Sessions are signed with `APP_SECRET_KEY` and last `DASHBOARD_SESSION_TTL_MINUTES`. Changing
     `DASHBOARD_PASSWORD` (or the username) signs out every existing session.
   - The session cookie is HttpOnly and SameSite=Lax. It is also Secure in production or over HTTPS.
   - Logging out revokes the session. The revocation is recorded in the request log, so it survives a
     restart when request logs are in MongoDB.
   - Request bodies are capped before they are read (uploads: `MAX_UPLOAD_MB` + 256 KB, other forms: 64 KB),
     and uploads without a valid session are refused before the file is received.
2. **Signed file links.**
   - `/files/{id}` serves a file only to a logged-in dashboard session, or for a link signed with HMAC
     (`?exp=...&sig=...`) that is valid for `FILE_URL_TTL_SECONDS` (15 minutes by default).
   - Archived documents return 404.
   - Cloudinary assets are uploaded as `authenticated`, so they have no public URL.
3. **Twilio signature.**
   - `/webhook` and `/webhook/status` check `X-Twilio-Signature` against `TWILIO_AUTH_TOKEN` (and
     `TWILIO_SECONDARY_AUTH_TOKEN` during token rotation).
   - The check is always on when `APP_ENV=production`.
4. **Sender allowlist.**
   - Only numbers in `AUTHORIZED_SENDERS` can use the bot. Other numbers get no reply.
   - If the list is empty, production rejects everyone unless you set `ALLOW_ANY_SENDER=true`, which is not
     recommended. Development allows any sender.
5. **Private confirmation.**
   - Private documents are sent only after the same number replies with a yes keyword (`YES`/`1` by
     default) within `PRIVATE_CONFIRMATION_TTL_SECONDS`, which is at least 15 seconds.
   - Natural replies also work: "yes please", "Yes 👍", "ok send" confirm; "no thanks" cancels.
   - Replying `NO` or `CANCEL` stops the request.
   - Sending a new request instead drops the pending confirmation. A reply with nothing to search for
     (for example "thanks") keeps it and repeats the question.
6. **Secrets and logs.**
   - Secrets live only in `.env` locally (git-ignored) or in Render's Environment tab.
   - Logs never contain raw message bodies. Numbers are masked (`+91******3210`).

## Using the dashboard

1. Open `http://127.0.0.1:8000/` locally, or `https://<service>.onrender.com/`, and sign in with
   `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`.
2. **Status panel.** Read any warnings first, such as "MongoDB unavailable" or "APP_SECRET_KEY not set". The
   panel also shows:
   - the active metadata and storage backends;
   - whether the WhatsApp sender is enabled;
   - whether signature checks are enforced.
3. **Upload.**
   - Choose a file of an allowed type, no larger than `MAX_UPLOAD_MB`.
   - Optionally set a category (for example `resume`, `id`, `certificate`) and comma-separated tags (for
     example `pan, tax`).
   - Tick **Private** for sensitive files.
4. **Documents.** Use **Open** to view a file (your session authorizes it) and **Archive** to remove a file
   from retrieval.
5. **Test a WhatsApp query.** Type what you would send on WhatsApp to see which document would match.
6. **Recent activity.** Shows the latest webhook, upload and audit events, with masked numbers.

## Using it on WhatsApp

Join the Twilio sandbox first: send `join <your-sandbox-code>` to the sandbox number. Then send messages like
these:

| You send | What happens |
|----------|--------------|
| `send my resume` | Resume sent as an attachment |
| `cv pls` | Same as above, because `cv` is a synonym of resume |
| `pan card` | PAN card file sent |
| `pasport` | Typo-tolerant: passport sent |
| `10th marksheet` | SSC marksheet sent |
| `hi` / `a` | "Document not found..." with a hint listing words you can try (for example "Try: resume, pan, passport") |
| `ok` / `yes` (nothing pending) | "No pending private confirmation..." (these are confirmation keywords, never searched) |

Private document flow:

```text
You:  send my passport
Bot:  🔒 passport.pdf is a private document. Reply 'YES' or '1' within 45 seconds to confirm. Reply 'NO' to stop.
You:  yes
Bot:  (passport arrives)
```

- **Yes keywords:** `yes, y, ok, confirm, 1, haan, ha`. **Cancel keywords:** `no, n, cancel, stop, 0`. You
  can change both lists (see the environment variables).
- **No pending confirmation.** A bare "yes" with nothing pending gets "No pending private confirmation..." and
  never sends anything.
- **Expired confirmation.** Replying after the time runs out gets "Private confirmation expired..."; ask
  again.
- **New request.** Sending a different request while a confirmation is pending cancels the old one and
  handles the new request normally.

If the attachment cannot be delivered, the bot sends a download link instead. The link works for
`FILE_URL_TTL_SECONDS`.

## API endpoints

When `DASHBOARD_AUTH_ENABLED=true`, endpoints marked **login** need a dashboard session cookie and return
401 without one.

| Method and path | Access | Purpose |
|-----------------|--------|---------|
| `GET /` | login (redirects to `/login`) | Dashboard |
| `GET /login`, `POST /login` | public | Login page and form |
| `GET /logout`, `POST /logout` | public | End the session |
| `GET /health` | public | Always 200: `status` is `ok` or `degraded`, plus `checks.metadata_store` and `checks.storage` |
| `GET /ready` | public | 200 when the metadata store is reachable, else 503 |
| `GET /setup/status` | login | Configuration flags, active backends and a `warnings` list |
| `POST /upload` | login | Multipart: `file`, `doc_category`, `tags`, `is_private` |
| `GET /documents` | login | Active documents (no server paths exposed) |
| `DELETE /documents/{id}` | login | Archive a document |
| `GET /get-document?query=...` | login | Test retrieval without WhatsApp |
| `GET /files/{id}` | login **or** signed link | Download or view a file |
| `GET /logs/recent?limit=20` | login | Recent request and audit logs |
| `GET /logs/delivery`, `GET /logs/delivery/summary` | login | Twilio delivery status per message |
| `POST /webhook` | Twilio signature | Incoming WhatsApp messages |
| `POST /webhook/status` | Twilio signature | Twilio delivery-status callbacks |
| `GET /docs`, `/redoc`, `/openapi.json` | public, **off in production** by default | API docs (`ENABLE_API_DOCS`) |

## Environment variables

Every setting has a default in [app/config.py](app/config.py) and a commented entry in
[.env.example](.env.example). **Bold** entries matter most in production.

### Application

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | `InstaRetriv AI` | Display name |
| **`APP_ENV`** | `development` | `development`, `test` or `production`. Production enforces Twilio signatures, disables silent fallbacks and hides API docs. |
| `HOST` | `127.0.0.1` | Bind host for `python run.py` |
| `PORT` | `8000` | Bind port for `python run.py` (Render injects its own `$PORT`) |
| **`APP_SECRET_KEY`** | empty | Signs sessions and file links. **Required in production.** When empty, a random per-process key is used, and sessions and links break after a restart. |
| **`PUBLIC_BASE_URL`** | empty | Public HTTPS URL without a trailing slash, for example `https://instaretriv-ai.onrender.com`. Used for signed links, the status callback and signature checks. When empty, it is derived from the request. |
| `ENABLE_API_DOCS` | unset | Unset: docs on outside production, off in production. `true`/`false` overrides that. |

### Dashboard

| Variable | Default | Description |
|----------|---------|-------------|
| `DASHBOARD_AUTH_ENABLED` | `true` | Set `false` only for local development. Everything becomes open, and the status panel warns about it. |
| `DASHBOARD_USERNAME` | `admin` | Login username |
| **`DASHBOARD_PASSWORD`** | empty | Login is disabled until this is set. Use a long, random password. |
| `DASHBOARD_SESSION_TTL_MINUTES` | `720` | Session lifetime |
| `LOGIN_MAX_ATTEMPTS` | `5` | Failed attempts per IP before a lockout |
| `LOGIN_LOCKOUT_MINUTES` | `15` | Lockout duration |

### WhatsApp and Twilio

| Variable | Default | Description |
|----------|---------|-------------|
| **`AUTHORIZED_SENDERS`** | empty | Comma-separated numbers, for example `whatsapp:+911234567890`. In production an empty list rejects everyone. |
| `ALLOW_ANY_SENDER` | `false` | Production with an empty allowlist: `true` allows anyone (not recommended) |
| **`TWILIO_ACCOUNT_SID`** | empty | Twilio Console, Account Info |
| **`TWILIO_AUTH_TOKEN`** | empty | Used to send messages and to validate signatures |
| `TWILIO_SECONDARY_AUTH_TOKEN` | empty | Also accepted for signature checks while you rotate tokens |
| `TWILIO_WHATSAPP_FROM` | `whatsapp:+14155238886` | Sender number (the sandbox by default) |
| `TWILIO_SEND_RETRIES` | `2` | Retries for transient send errors (4xx errors are not retried) |
| `REQUIRE_TWILIO_SIGNATURE` | `false` | Check signatures outside production too (always on in production) |
| `PRIVATE_CONFIRMATION_TTL_SECONDS` | `45` | Time allowed to confirm a private document (minimum 15) |
| `PRIVATE_CONFIRMATION_YES_KEYWORDS` | `yes,y,ok,confirm,1,haan,ha` | Confirm keywords |
| `PRIVATE_CONFIRMATION_CANCEL_KEYWORDS` | `no,n,cancel,stop,0` | Cancel keywords |
| `FILE_URL_TTL_SECONDS` | `900` | Lifetime of signed `/files` links |

### Storage

| Variable | Default | Description |
|----------|---------|-------------|
| **`STORAGE_BACKEND`** | `local` | `local` or `cloudinary`. Use `cloudinary` in production, because Render's disk is wiped on every deploy. |
| `UPLOAD_DIR` | `uploads` | Local storage folder |
| `ALLOWED_EXTENSIONS` | `pdf,png,jpg,jpeg,webp,doc,docx` | Accepted file types |
| `MAX_UPLOAD_MB` | `15` | Upload size limit (WhatsApp's media limit is 16 MB) |
| **`CLOUDINARY_CLOUD_NAME`**, **`CLOUDINARY_API_KEY`**, **`CLOUDINARY_API_SECRET`** | empty | Cloudinary Console, Dashboard, API Keys |

### Metadata and logs

| Variable | Default | Description |
|----------|---------|-------------|
| **`METADATA_BACKEND`** | `json` | `json` or `mongo`. Use `mongo` in production. |
| `METADATA_FILE` | `data/metadata.json` | JSON metadata file |
| `REQUEST_LOG_FILE` | `data/request_logs.json` | JSON request-log file |
| `REQUEST_LOG_MAX_ENTRIES` | `2000` | Maximum entries kept in the JSON log |
| **`MONGODB_URI`** | empty | `mongodb+srv://user:password@cluster.../?retryWrites=true&w=majority` |
| `MONGODB_DATABASE` | `instaretriv_ai` | Database name |
| `MONGODB_COLLECTION` | `documents` | Document metadata collection |
| `MONGODB_REQUEST_LOG_COLLECTION` | `request_logs` | Request-log collection (entries expire after 30 days) |
| `MONGODB_TIMEOUT_MS` | `5000` | Connection and server-selection timeout |
| `METADATA_STORE_RETRY_SECONDS` | `30` | How often to retry an unreachable metadata store |

## Local setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
# Edit .env: at least set DASHBOARD_PASSWORD (and APP_SECRET_KEY if you want sessions to survive restarts)
python run.py
```

Then open http://127.0.0.1:8000/ and sign in.

- `python run.py` uses `HOST` and `PORT` from `.env`. It auto-reloads only when `APP_ENV=development`.
- You can also use `.\scripts\start_dev.ps1`, adding `-Reload` for auto-reload or `-Port 9000` for another
  port.
- `.\scripts\check_setup.ps1` checks `/health` and `/ready` on a running instance. Add
  `-BaseUrl https://<service>.onrender.com` to check the deployed service.
- On macOS or Linux, use `source .venv/bin/activate` and `cp .env.example .env`.

The local defaults (`METADATA_BACKEND=json`, `STORAGE_BACKEND=local`) need no cloud accounts.

### Testing WhatsApp locally (optional)

1. Run `.\scripts\setup_ngrok.ps1`. It downloads ngrok into `ngrok/`, which git ignores.
2. Run `.\ngrok\ngrok.exe config add-authtoken <token>` once.
3. Run `.\ngrok\ngrok.exe http 8000`.
4. Set these in `.env`:
   - `PUBLIC_BASE_URL=https://<id>.ngrok-free.app`
   - your Twilio values
   - `AUTHORIZED_SENDERS=whatsapp:+<your number>`
   - optionally, `REQUIRE_TWILIO_SIGNATURE=true`

   Then restart the app.
5. In the Twilio Console sandbox settings, set **When a message comes in** to
   `https://<id>.ngrok-free.app/webhook` (HTTP POST).

## Testing

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

The test suite is hermetic. [tests/conftest.py](tests/conftest.py) forces JSON and local backends, empty
credentials and temporary folders. It makes no network calls to Twilio, MongoDB or Cloudinary.

## Deploying on Render (step by step)

### 1. MongoDB Atlas (metadata)

1. Sign in at https://cloud.mongodb.com, then **Create** a cluster. The free **M0** tier is enough.
2. Go to **Database Access**, then **Add New Database User**. Choose password authentication with a long,
   random password, and give the user **Read and write to any database**. If you prefer, limit it to
   `readWrite` on `instaretriv_ai`.
3. Go to **Network Access**, then **Add IP Address**, then `0.0.0.0/0`. Render's free plan has no static
   outbound IPs, so the strong database password is your protection.
4. Go to **Database**, then **Connect**, then **Drivers**, and copy the `mongodb+srv://...` string. Put your
   user and password into it; URL-encode special characters in the password. This is your `MONGODB_URI`.

> Atlas pauses or deletes inactive free clusters. If production suddenly answers "Document not found" for
> everything, or says it can't reach its database, check that the cluster still exists. See
> Troubleshooting.

### 2. Cloudinary (files)

1. Create an account at https://cloudinary.com.
2. Copy the **Cloud name**, **API key** and **API secret** from **Dashboard** or **Settings, API Keys**.

The app uploads files as private `authenticated` assets under `instaretriv/` and streams them itself, so
no file has a public URL.

### 3. Render web service

1. Push this branch to GitHub.
2. On https://dashboard.render.com, choose **New**, then **Blueprint**, and select the repository. Render
   reads [render.yaml](render.yaml), which sets the following:
   - the start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`;
   - the health check on `/health`;
   - Python 3.12.8 (`PYTHON_VERSION`; [.python-version](.python-version) also pins it);
   - `APP_ENV=production`, `DASHBOARD_AUTH_ENABLED=true`, `REQUIRE_TWILIO_SIGNATURE=true`,
     `STORAGE_BACKEND=cloudinary`, `METADATA_BACKEND=mongo` and `MAX_UPLOAD_MB=15`;
   - a generated `APP_SECRET_KEY`.

   (Using a manual **New, Web Service** instead? Use the same build and start commands and set the same
   variables by hand.)
3. When Render prompts for the secret (`sync: false`) values, fill in:
   - `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`;
   - `AUTHORIZED_SENDERS`, for example `whatsapp:+911234567890`;
   - `MONGODB_URI`;
   - `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` and `CLOUDINARY_API_SECRET`;
   - `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`, plus `TWILIO_SECONDARY_AUTH_TOKEN` (optional);
   - `TWILIO_WHATSAPP_FROM`, which is `whatsapp:+14155238886` for the sandbox;
   - `PUBLIC_BASE_URL=https://<service>.onrender.com`.
4. Deploy. When it is live, check these:
   - `https://<service>.onrender.com/health` shows `"status": "ok"`.
   - `https://<service>.onrender.com/ready` returns 200.
   - After you log in at `https://<service>.onrender.com/`, the status panel shows **no warnings**.

   You can also run `.\scripts\check_setup.ps1 -BaseUrl https://<service>.onrender.com`.

### 4. Twilio WhatsApp sandbox

1. Open the Twilio Console and go to **Messaging, Try it out, Send a WhatsApp message**, then **Sandbox
   settings**.
2. Set **When a message comes in** to `https://<service>.onrender.com/webhook`, method **HTTP POST**.
3. Set **Status callback URL** to `https://<service>.onrender.com/webhook/status`, method **HTTP POST**.
   The app also sets this callback on every message it sends.
4. Save. From your phone, send `join <sandbox-code>` to the sandbox number, then try `send my resume`.

The URL in Twilio must match `PUBLIC_BASE_URL` exactly (https, same host, no trailing slash), because the
signature check depends on it.

### 5. Upload your documents

Log in to the dashboard and upload your files again. Documents from an old or deleted Atlas cluster are
not migrated automatically.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| **"Document not found" for everything** | 1. Log in and read the **status panel warnings** (`/setup/status`). The usual causes are an unreachable MongoDB (cluster deleted or paused, wrong `MONGODB_URI`, Atlas network access missing `0.0.0.0/0`) or an empty document list. 2. Check that `/ready` returns 200. 3. Check that **Documents** in the dashboard lists your files, and re-upload them if the database is new. 4. Use **Test a WhatsApp query** to see how your wording matches. |
| WhatsApp replies "InstaRetriv can't reach its document database right now" | MongoDB is unreachable. Fix the cluster, URI or network access as above. The app retries automatically every `METADATA_STORE_RETRY_SECONDS`. |
| No reply at all | 1. Is your number in `AUTHORIZED_SENDERS`? Other numbers get no reply. 2. Is the Twilio webhook URL exactly `https://<service>.onrender.com/webhook` (POST)? 3. Does `PUBLIC_BASE_URL` match it? Otherwise the signature check returns 403; Twilio Console, Monitor, Logs, Errors shows error 11200. 4. Has your sandbox membership expired (below)? |
| First message after a quiet period is slow or lost | **Cold start.** Render's free plan sleeps after about 15 minutes idle and needs 30-60 seconds to wake. Twilio waits only 15 seconds. Send the message again after a minute. For instant replies, use a paid instance. Pinging `/health` from an uptime monitor keeps a free instance awake but uses free-tier hours. |
| Sandbox stopped answering after a few days | The Twilio sandbox needs you to **re-join** periodically; sandbox sessions expire after about 72 hours. Send `join <sandbox-code>` again. |
| Error 63016 in Twilio logs / bot can't message you | WhatsApp's **24-hour session window**. The bot can only reply freely within 24 hours of your last message. Send any message first. |
| Attachment didn't arrive but a link did | Twilio couldn't fetch or send the media (size, type or a temporary error). The link works for `FILE_URL_TTL_SECONDS`. Check `/logs/delivery` in the dashboard. |
| A link says 403 / 404 | 403: the link expired or was altered, so request the file again. 404: the document was archived or its stored file is missing, so re-upload it. |
| Upload fails with 503 | Cloudinary or MongoDB is unavailable or not configured. Read the status panel warnings. |
| Upload fails with 413 / 400 | The file is larger than `MAX_UPLOAD_MB`, empty, or of a type not in `ALLOWED_EXTENSIONS`. |
| Login form is disabled | `DASHBOARD_PASSWORD` is not set. Set it and restart. |
| Login page says you're locked out (HTTP 429) | Lockout after `LOGIN_MAX_ATTEMPTS` failures. Wait `LOGIN_LOCKOUT_MINUTES`. |
| Logged out after every deploy | `APP_SECRET_KEY` is empty, so a random key is generated on each start. Set a fixed value. |
| Private file "confirmation expired" | Reply within the time shown in the prompt (`PRIVATE_CONFIRMATION_TTL_SECONDS`). |

## Project structure

```text
app/
  main.py                    FastAPI app, routes, webhook flow
  config.py                  Settings (environment variables)
  security.py                sessions, signed links, Twilio signature, log masking
  stores.py                  metadata/log store factories with availability status
  repository.py              JSON metadata repository (atomic writes)
  repository_mongo.py        MongoDB metadata repository
  request_log_repository.py  JSON and MongoDB request logs
  schemas.py                 API models
  ui_templates.py            login page and dashboard HTML
  services/
    matcher.py               query matching (synonyms, fuzzy)
    tagging.py               filename tags and category suggestions
    storage.py               local and Cloudinary storage, streaming
    whatsapp.py              Twilio sender with retries
scripts/                     start_dev.ps1, check_setup.ps1, setup_ngrok.ps1
tests/                       pytest suite
```

See [ROADMAP.md](ROADMAP.md) for what's next.
