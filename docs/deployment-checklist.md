# Deployment checklist: real Rekognition and real email

Use this right after deploying `feat/secure-accounts-and-search` to the live
server. Use test accounts with real inboxes you control, and delete them
at the end. Work through it in order; stop and roll back (database/SETUP.md)
if a step fails.

## Before starting the app
- [ ] The database is migrated: `SELECT version FROM schema_migrations;` ends with `005_reconcile_indexed_photos`.
- [ ] `python backend/indexing_report.py` output is saved from **before** and **after** migrating; `searchable` did not go down.
- [ ] Env set: `SESSION_SECRET`, `TAM_PRODUCTION=1`, `TAM_EMAIL_PROVIDER`, `TAM_EMAIL_FROM`, `TAM_ADMIN_NOTIFY_EMAIL` (plus `TAM_SMTP_*` for SMTP).
- [ ] `TAM_REKOGNITION_COLLECTION_ID` is **not** set (the live collection `tam-diwali-2026-faces` is used, and nothing is auto-created).
- [ ] `ADMIN_SECRET` removed from the environment.
- [ ] SES only: sender verified in ca-central-1, account out of the SES sandbox (or the recipient verified), and the IAM role/keys allow `ses:SendEmail`.
- [ ] IAM still allows `rekognition:IndexFaces`, `SearchFacesByImage`, `DetectFaces` and S3 `GetObject`/`PutObject` on the bucket.

## After starting the app
- [ ] The log shows no `Email is not configured` warning and no errors at startup.
- [ ] `https://…/find-my-photos` and `/gallery` redirect to `/attendee/login` in a private window.

## Email
- [ ] Register a test attendee. Within ~1 minute the admin inbox gets one digest naming them, with an Approve link and a link to the pending list.
- [ ] Register two more within 5 minutes: they arrive together in **one** later digest.
- [ ] Open the Approve link while logged out: you are sent to the admin login. Log in, open the link again, click **Approve**: the account is approved. Open the link again: "Link no longer valid".
- [ ] On the attendee login page, use "Forgot your password?": the reset email arrives, the link works once, and the new password logs in.
- [ ] Check the spam folder. If mail lands there, set up SPF/DKIM for the sender domain.

## Rekognition
- [ ] Manage Events: decide on and set "Automatically authorize uploaded photos for face indexing".
- [ ] As an existing contributor (after **Set login**), upload one photo with a consenting tester's face.
  - Switch on: Face Indexing page no longer lists it within seconds.
  - Switch off: it is listed; **Authorize & index** it.
- [ ] `indexing_report.py`: `searchable` went up by 1, `failed_jobs` and `stuck_processing` are 0.
- [ ] As the approved test attendee: give consent, take a selfie, see the photo, download it (watermarked).
- [ ] A selfie of someone not in any photo returns "No matching photographs found".
- [ ] An existing photo indexed before the deploy is still found by a matching selfie (proves migration kept old faces).
- [ ] Disable the test attendee from Manage Attendees: their open photo page now fails (401).

## Clean up
- [ ] Delete the test attendee(s). Remove the test photo from S3 and the database if it should not stay in the event.
- [ ] Save the final `indexing_report.py` output with the deployment notes.
