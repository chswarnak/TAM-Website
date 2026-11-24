# feat/secure-accounts-and-search

Plan: see handoff.md (approved 2026-11-23). Test first for every step.

- [ ] 1. Test harness, idempotent migrations, schema.sql in sync, gallery router + schema fix
- [ ] 2. settings.py (event id, collection id default pinned, origin/https + production guard)
- [ ] 3. Logging, narrowed exceptions, no print()
- [ ] 4. Approve permission check; ADMIN_SECRET routes -> admin session
- [ ] 5. Accounts (email/username), ID-only upload path, "Set login" for existing contributors
- [ ] 6. Login rate limiting (env-configurable)
- [ ] 7. Attendee accounts; login-required consent/search/photos/gallery; anonymous routes removed
- [ ] 8. Account tokens, digest emails (SES/SMTP), dashboard approve selected/all
- [ ] 9. Password reset (admin temp password + self-service when email configured)
- [ ] 10. Face-indexing switch, admin authorize page, indexing after upload
- [ ] 11. Upload page shows real failure reasons
- [ ] 12. End-to-end selfie test (fake Rekognition), local run, docs
