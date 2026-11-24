# Handoff — TAM Website

_Last updated: 2026-11-23. Branch: `feat/secure-accounts-and-search` (not pushed)._

## What was done this session
1. Reviewed the project and ran it locally. Found: the gallery returned 500s
   (the schema was behind the code), the admin gallery router wasn't mounted,
   photos were never indexed, approve had no permission check, path traversal
   via the contributor name, no login rate limiting, and legacy ADMIN_SECRET routes.
2. Implemented the approved plan (see tasks/todo.md), one commit per step:
   - idempotent migrations (`backend/migrate.py`); `schema.sql` is generated;
   - `settings.py` (all env config; production guard);
   - email/username accounts for contributors and the new attendee accounts;
     everyone needs admin approval;
   - attendee login required for consent, search, photos and gallery; the
     anonymous flows are removed;
   - single-use emailed approval links, digest emails (SES/SMTP), bulk approval;
   - admin temporary passwords and self-service reset;
   - per-event face-indexing switch and admin authorize page; indexing after upload;
   - logging instead of print/broad except; per-file upload errors.

## Design decisions (why)
- Kept the `contributors` table (photos FK, ~60 queries) and added an `attendees`
  table. Shared logic lives in `accounts.py`; table names come from a whitelist.
- Migrations are Python, not SQL, because the live DB was hand-patched. Each
  step checks `PRAGMA table_info` first.
- The approval-link GET only shows a confirmation page; approving needs a POST,
  because email scanners prefetch links.
- Collection ID default pinned to `tam-diwali-2026-faces`; a collection is only
  auto-created when the name is set explicitly (user requirement).
- Search results are reused from `guest_search_sessions`, now bound to `attendee_id`;
  the token lives in the signed session cookie.
- The gallery access code and anonymous registration were removed (login replaces them).

## Next steps
- Deploy: follow database/SETUP.md "Upgrade an existing database", then set
  the env vars (README table). Configure SES (verify sender, leave sandbox)
  or SMTP, then set `TAM_EMAIL_PROVIDER`.
- Before the event: the Super Admin uses "Set login" for every existing contributor.
- Legal/privacy: fill in `PRIVACY_CONTACT` and the retention policies in consent_service.py.
- Not built: a UI to grant organizer permissions (organizer_permissions rows
  must be inserted by hand); attendee disable/delete; the Face Indexing page
  previews require Super Admin (organizers with can_manage_photos see broken thumbnails).
- Real AWS (Rekognition, SES) not tested: no credentials locally.

## Blockers
- None in code. Email cannot be verified until the provider is configured.

## Local run recipe
    TAM_DB_PATH=$PWD/local-data/migrated.db TAM_ALLOWED_ORIGIN=http://127.0.0.1:8000 \
    TAM_REQUIRE_HTTPS=0 SESSION_SECRET=dev python -I <scratchpad>/run/run_local.py backend
(The launcher injects tests/fakes.py for S3/Rekognition; it is intentionally
not committed.)
