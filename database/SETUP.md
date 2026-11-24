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

## Regenerate schema.sql after adding a migration

    python backend/migrate.py --dump-schema database/schema.sql

## Security
- Never commit SQLite database files, backups, `.env` files or secrets.
- Keep production attendee information private.
- Use a separate database for development and testing.
