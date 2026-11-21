# TAM Smart Event Photo Sharing System

A private event photo-sharing application developed for the
Telugu Association of Montreal (TAM).

## Overview

The system allows approved contributors to upload event photographs
and enables attendees to find their photographs using a selfie-based
search process, subject to attendee consent and authorization.

## Main Features

### Administration
- Super Admin and organizer accounts
- Contributor approval and management
- Event management
- Upload controls
- Attendee consent management

### Contributors
- Contributor login and dashboard
- Event Camera for smartphone photography
- Multiple-photo upload
- Administrator-controlled upload permissions

### Attendees
- Event portal
- Attendee consent process
- Selfie-based photo search
- Authorized photo access

### AWS Integration
- Amazon S3 for private photo storage
- Amazon Rekognition for face matching
- EC2 hosting
- SQLite database

## Technology Stack

- Python
- FastAPI
- Uvicorn
- SQLite
- Jinja2 HTML templates
- Amazon S3
- Amazon Rekognition
- Pillow

## Project Structure

backend/
    main.py
    database.py
    guest_sessions.py
    requirements.txt
    templates/

database/
    schema.sql
    SETUP.md

## Installation

1. Clone this repository.
2. Create a Python virtual environment.
3. Install dependencies from backend/requirements.txt.
4. Follow database/SETUP.md to initialize a new database.
5. Configure required environment variables and AWS permissions.
6. Start the FastAPI application after completing configuration.

## Security

- Never commit production database files.
- Never commit .env files, access tokens, or private keys.
- Keep S3 objects private.
- Use least-privilege AWS IAM permissions.
- Obtain appropriate attendee consent before facial indexing or matching.
- Review authentication and authorization before production deployment.

## Database

The database schema is available in database/schema.sql.

The application uses TAM_DB_PATH to select its SQLite database file.

## Project Status

Under active development.

## Organization

Telugu Association of Montreal (TAM)
