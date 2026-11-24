"""
TAM Smart Photos — Contributor Consent Management.

Contributor acknowledgement is separate from attendee
facial-recognition and selfie-search consent.

Only token hashes are stored in SQLite.
"""

import hashlib
import secrets
import sqlite3

from datetime import datetime, timedelta, timezone

from database import DB_PATH


CONTRIBUTOR_CONSENT_COOKIE = "tam_contributor_consent"

CONTRIBUTOR_CONSENT_VERSION = "2026-11-v1"

CONTRIBUTOR_CONSENT_MAX_AGE = 24 * 60 * 60


def create_contributor_consent():
    """
    Create a new contributor acknowledgement.

    The caller must first verify an explicit user
    acknowledgement and the request's origin.
    """

    token = secrets.token_urlsafe(32)

    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=CONTRIBUTOR_CONSENT_MAX_AGE)
    ).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB_PATH, timeout=15) as connection:
        connection.execute(
            """
            INSERT INTO contributor_consent_acknowledgements
                (
                    token_hash,
                    consent_version,
                    expires_at
                )
            VALUES (?, ?, ?)
            """,
            (
                token_hash,
                CONTRIBUTOR_CONSENT_VERSION,
                expires_at
            )
        )

    return token


def verify_contributor_consent(request):
    """
    Verify an acknowledgement using its secure cookie.

    Invalid, expired, revoked, and outdated acknowledgements
    are rejected.
    """

    token = request.cookies.get(
        CONTRIBUTOR_CONSENT_COOKIE
    )

    if not token or len(token) > 256:
        return False

    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    with sqlite3.connect(DB_PATH, timeout=10) as connection:

        row = connection.execute(
            """
            SELECT id
            FROM contributor_consent_acknowledgements
            WHERE token_hash = ?
              AND consent_version = ?
              AND expires_at > CURRENT_TIMESTAMP
              AND revoked_at IS NULL
            LIMIT 1
            """,
            (
                token_hash,
                CONTRIBUTOR_CONSENT_VERSION
            )
        ).fetchone()

    return row is not None
