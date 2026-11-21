# TAM Website - Database Setup

## Overview
TAM Website uses SQLite for application data.
The database structure is provided in schema.sql.
Production database files must never be uploaded to GitHub.

## Database Configuration
The backend uses the TAM_DB_PATH environment variable.
If unset, it defaults to backend/smart_event.db.

## Create a Development Database
Run these commands from the repository root:

    mkdir -p local-data
    sqlite3 local-data/smart_event.db < database/schema.sql

Use this only for a new, empty development database.
Never run schema initialization against the production database.

## Configure the Database Path
From the repository root, run:

    export TAM_DB_PATH="$PWD/local-data/smart_event.db"

## Verify the Database
Run:

    sqlite3 local-data/smart_event.db ".tables"

The exported schema contains 13 application tables.

## Additional Setup
Administrator account creation and AWS configuration are separate steps.
Review application configuration before starting the backend.

## Security
- Never commit SQLite database files.
- Never commit .env files or authentication secrets.
- Keep production attendee information private.
- Use a separate database for development and testing.
