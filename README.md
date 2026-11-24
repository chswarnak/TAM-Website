# TAM Smart Event Photo Sharing System

A private event photo-sharing application developed for the
Telugu Association of Montreal (TAM).

## Overview

Approved contributors upload event photographs. Approved, logged-in
attendees give biometric consent and find the photographs they appear in
with a selfie. Nobody can use any feature until the Super Admin (or an
organizer with permission) approves their account.

## Main Features

### Accounts and approval
- Contributors and attendees register with a full name (display only), an
  email (required, unique) and an optional username (unique, no spaces).
  Login accepts the email or the username, never the full name.
- Every new account stays pending until an admin approves it. The admin
  dashboard shows pending accounts with Approve selected and Approve all.
- New registrations are emailed to the Super Admin in batches (at most one
  email every few minutes), each with a signed, single-use Approve link.
- Admins can issue a temporary password, and the user must change it at
  their next login. When email is configured, users can also reset their
  own password with a single-use email link.
- Failed logins are rate-limited per account and per IP.

### Administration
- Super Admin and organizer accounts (organizer permissions:
  `can_approve_contributors`, `can_manage_photos`)
- Contributor and attendee management, event management, upload controls
- Face indexing: a per-event switch authorizes uploads automatically, or
  admins authorize photos on the Face Indexing page
- Attendee consent register and export

### Contributors
- Event Camera and multi-photo upload (each failed file shows its reason)

### Attendees
- Biometric consent recorded against the attendee's account
- Selfie search, then matched photos (watermarked thumbnails and downloads)
- Event gallery (requires login)

## Technology Stack

Python, FastAPI, Uvicorn, SQLite, Jinja2, Amazon S3, Amazon Rekognition,
Amazon SES or SMTP (optional), Pillow, Argon2.

## Project Structure

    backend/
        main.py                  app setup, admin pages, uploads, events, AWS page
        settings.py              all environment configuration
        migrate.py, migrations.py   database migrations
        accounts.py, account_routes.py         registration, login, passwords
        admin_auth.py, admin_account_routes.py admin auth, approvals, resets
        attendee_routes.py, guest_sessions.py, consent_service.py   consent + search
        face_indexing.py, face_indexing_routes.py, rekognition_service.py
        notifications.py, account_tokens.py    email digest, single-use links
        gallery_*.py             event gallery
        rate_limit.py, http_security.py
        templates/, static/
        tests/                   pytest suite (fake S3/Rekognition in tests/fakes.py)
    database/
        schema.sql (generated), migrations/000_baseline.sql, SETUP.md

## Installation

1. Create a Python virtual environment.
2. `pip install -r backend/requirements.txt -r backend/requirements-dev.txt`
3. Follow `database/SETUP.md` to create or migrate the database.
4. Set the environment variables below.
5. From `backend/`: `uvicorn main:app --host 127.0.0.1 --port 8000`

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `SESSION_SECRET` | required | Signs sessions, CSRF tokens and email links |
| `TAM_DB_PATH` | `backend/smart_event.db` | SQLite file |
| `TAM_PRODUCTION` | unset | `1` makes cookies HTTPS-only and refuses relaxed origin/HTTPS settings |
| `TAM_EVENT_ID` | `tam-diwali-2026` | Current event |
| `TAM_REKOGNITION_COLLECTION_ID` | `tam-diwali-2026-faces` | Face collection. It is created automatically only when this is set explicitly. |
| `TAM_ALLOWED_ORIGIN` | `https://tam-smartphotos.duckdns.org` | Only origin accepted for consent and selfie search |
| `TAM_REQUIRE_HTTPS` | `1` | Require `X-Forwarded-Proto: https` for selfie search |
| `TAM_PUBLIC_BASE_URL` | allowed origin | Base URL used in emailed links |
| `TAM_UPLOAD_DIR` | `backend/uploads` | Local copy of uploads |
| `TAM_LOG_LEVEL` | `INFO` | Logging level |
| `TAM_LOGIN_MAX_FAILURES_PER_IDENTIFIER` | `5` | Failed logins per account+IP per window |
| `TAM_LOGIN_MAX_FAILURES_PER_IP` | `500` | Failed logins per IP per window (the venue shares one IP) |
| `TAM_LOGIN_WINDOW_MINUTES` | `15` | Rate-limit window |
| `TAM_EMAIL_PROVIDER` | unset | `ses` or `smtp`. When unset, no emails are sent. |
| `TAM_EMAIL_FROM` | unset | Sender address (must be verified in SES) |
| `TAM_ADMIN_NOTIFY_EMAIL` | unset | Where registration digests go |
| `TAM_NOTIFY_INTERVAL_MINUTES` | `5` | At most one digest per interval |
| `TAM_SMTP_HOST` / `TAM_SMTP_PORT` / `TAM_SMTP_USERNAME` / `TAM_SMTP_PASSWORD` | — / `587` | SMTP settings (STARTTLS) |
| `TAM_USE_IAM_ROLE` | unset | `1` uses the EC2 instance role when `backend/.env` has no AWS keys |

For local testing of selfie search over plain HTTP:
`TAM_ALLOWED_ORIGIN=http://127.0.0.1:8000 TAM_REQUIRE_HTTPS=0`.

## Tests

    cd backend && python -m pytest -q

## Security

- Never commit production database files, `.env` files, access tokens or private keys.
- Keep S3 objects private, and use least-privilege IAM permissions
  (Rekognition, S3, and `ses:SendEmail` if using SES).
- Obtain appropriate attendee consent before facial indexing or matching.
- Fill in the privacy contact and retention policies in `backend/consent_service.py`
  before collecting consent in production.

## Organization

Telugu Association of Montreal (TAM)
