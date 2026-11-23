"""
TAM Event Gallery — Guest Session Management.

Gallery sessions are separate from facial-recognition sessions.
They provide temporary access to approved event media.
"""

import hashlib
import secrets
import sqlite3
import time


GALLERY_SESSION_DURATION = 30 * 60


def hash_gallery_token(token: str) -> str:
    """Store only a SHA-256 hash of the session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_gallery_session(
    connection: sqlite3.Connection,
    event_id: str,
    attendee_name: str,
    attendee_mobile: str,
) -> str:
    """Create a temporary gallery session for a registered attendee."""

    name = attendee_name.strip()
    mobile = attendee_mobile.strip()

    if not name or not mobile:
        raise ValueError("Attendee name and mobile number are required")

    token = secrets.token_urlsafe(32)
    token_hash = hash_gallery_token(token)

    now = int(time.time())

    with connection:
        connection.execute(
            """
            INSERT INTO gallery_guest_sessions (
                token_hash,
                event_id,
                attendee_name,
                attendee_mobile,
                created_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token_hash,
                event_id,
                name,
                mobile,
                now,
                now + GALLERY_SESSION_DURATION,
            ),
        )

    return token


def validate_gallery_session(
    connection: sqlite3.Connection,
    token: str,
    event_id: str,
):
    """
    Validate a temporary gallery session.

    Return attendee information only if the session exists,
    belongs to the requested event, and has not expired.
    """

    if not token or not isinstance(token, str):
        return None

    if len(token) > 256:
        return None

    token_hash = hash_gallery_token(token)
    now = int(time.time())

    connection.row_factory = sqlite3.Row

    session = connection.execute(
        """
        SELECT
            token_hash,
            event_id,
            attendee_name,
            attendee_mobile,
            created_at,
            expires_at
        FROM gallery_guest_sessions
        WHERE token_hash = ?
          AND event_id = ?
          AND expires_at > ?
        """,
        (token_hash, event_id, now),
    ).fetchone()

    if session is None:
        return None

    return dict(session)
