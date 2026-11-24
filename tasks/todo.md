# feat/secure-accounts-and-search

Plan approved 2026-11-23 (see handoff.md). Test first for every step.

- [x] 1. Test harness, idempotent migrations, schema.sql in sync, gallery router + schema fix
- [x] 2. settings.py (event id, collection id default pinned, origin/https + production guard)
- [x] 3. Logging, narrowed exceptions, no print()
- [x] 4. Approve permission check; ADMIN_SECRET routes -> admin session
- [x] 5. Accounts (email/username), ID-only upload path, "Set login" for existing contributors
- [x] 6. Login rate limiting (env-configurable)
- [x] 7. Attendee accounts; login-required consent/search/photos/gallery; anonymous routes removed
- [x] 8. Account tokens, digest emails (SES/SMTP), dashboard approve selected/all
- [x] 9. Password reset (admin temp password + self-service when email configured)
- [x] 10. Face-indexing switch, admin authorize page, indexing after upload
- [x] 11. Upload page shows real failure reasons
- [x] 12. End-to-end selfie test (fake Rekognition), local run, docs

## Review
- 108 tests pass (`cd backend && python -m pytest -q`).
- Local run against a migrated copy of the old dev DB (fake S3/Rekognition)
  verified: anonymous access refused; legacy name login refused; Set login,
  approve-all, upload + auto indexing, consent, selfie match (1 of 2
  photos), download 200, other photo 403, gallery 200 for attendee.
- Not verified: real AWS Rekognition/SES/SMTP (no credentials in backend/.env).
- TDD deviation: rekognition_service.py was rewritten before its tests
  (written right after, in the same commit).
