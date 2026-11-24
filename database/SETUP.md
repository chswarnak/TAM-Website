# TAM Website - Database Setup

## Overview
TAM Website uses SQLite. The schema is created and upgraded by
`backend/migrate.py`, which applies the versioned, idempotent migrations
in `backend/migrations.py` (the baseline is `database/migrations/000_baseline.sql`).

`database/schema.sql` is the fully migrated schema, for reference only. It is
generated, and a test keeps it in sync, so don't edit it by hand.

Production database files must never be committed.

## Database location
The backend reads the `TAM_DB_PATH` environment variable.
If it is unset, the default is `backend/smart_event.db`.

## Create a development database
From the repository root:

    mkdir -p local-data
    export TAM_DB_PATH="$PWD/local-data/smart_event.db"
    python backend/migrate.py

Then create the first Super Admin (interactive):

    cd backend && python create_super_admin.py

## Upgrade an existing database (including the live server)
`migrate.py` is safe to run repeatedly. Before changing anything, it copies
the database to `<db>.pre-migrate-<UTC timestamp>.bak` next to it.

1. Stop the app (e.g. `sudo systemctl stop <service>`), so nothing writes
   to the database during the migration.
2. Make your own extra copy too:
   `sqlite3 "$TAM_DB_PATH" ".backup '/home/<user>/smart_event-$(date +%F).db'"`
3. Deploy the new code, then run, with the same environment as the app:
   `python backend/migrate.py`
   (or `python backend/migrate.py --db /path/to/smart_event.db`)
4. Check the result:
   `sqlite3 "$TAM_DB_PATH" "SELECT version FROM schema_migrations;"`
   This should list `000_baseline` through the latest migration.
5. Start the app again.

To roll back, stop the app and restore the `.bak` file (or your own copy)
over the database file, then deploy the previous code.

The migrations only add tables, columns and indexes. They never drop data.
Columns that were already added by hand (for example
`events.gallery_enabled`) are detected and left alone.

## Photos already uploaded or indexed on the live server
Migration `005_reconcile_indexed_photos` makes photos indexed before this
release consistent **without calling AWS**:

| State before migrating | After migrating |
|---|---|
| Authorized, faces stored, job `completed` | Unchanged and still searchable |
| Faces stored but job missing or stuck in `processing` | Job set to `completed`, so the photo becomes searchable and is never indexed again |
| Job stuck in `processing` with no faces stored | Job set to `failed`, so it is listed on Face Indexing and can be retried |
| Uploaded but never authorized | Unchanged; listed on Face Indexing for an admin to authorize |
| Faces stored but not authorized | Not searchable. Authorizing it on Face Indexing does **not** re-index it (stored faces are reused). |

Indexing also refuses to send a photo to Rekognition again when faces are
already stored for it in the configured collection, so no photo is indexed
twice. Keep `TAM_REKOGNITION_COLLECTION_ID` unset so the live collection
(`tam-diwali-2026-faces`) stays in use.

Check the state before and after migrating (read-only):

    python backend/indexing_report.py > before.txt
    python backend/migrate.py
    python backend/indexing_report.py > after.txt
    diff before.txt after.txt

`searchable` must not go down. Any `not_authorized` photos are waiting for
an admin on the Face Indexing page (or for the event's automatic switch).

## Regenerate schema.sql after adding a migration

    python backend/migrate.py --dump-schema database/schema.sql

## Security
- Never commit SQLite database files, backups, `.env` files or secrets.
- Keep production attendee information private.
- Use a separate database for development and testing.
