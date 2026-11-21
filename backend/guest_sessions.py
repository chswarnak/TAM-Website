
"""
TAM Smart Event Photo Sharing System
Step 163T - Secure Temporary Guest Sessions

This module does not expose public API endpoints.
Only trusted backend code may create guest sessions
after verifying attendee consent and selfie matches.
"""

import hashlib
import secrets
import sqlite3
import time


SESSION_DURATION_SECONDS = 30 * 60

# Temporary consent-to-selfie session settings.
CONSENT_FLOW_DURATION_SECONDS = 15 * 60
CONSENT_FLOW_COOKIE_NAME = "tam_guest_consent"


def hash_guest_token(token: str) -> str:
    """Hash a guest session token before database storage."""
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def initialize_guest_session_tables(conn: sqlite3.Connection):
    """Create isolated guest-session tables if needed."""

    conn.execute("PRAGMA foreign_keys = ON")

    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS guest_search_sessions (
                token_hash TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                consent_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS guest_consent_flows (
                flow_hash TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                consent_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (consent_id)
                    REFERENCES attendee_consents(id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_guest_consent_flows_expiry
            ON guest_consent_flows(expires_at)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS guest_session_photos (
                token_hash TEXT NOT NULL,
                photo_id INTEGER NOT NULL,
                PRIMARY KEY (token_hash, photo_id),
                FOREIGN KEY (token_hash)
                    REFERENCES guest_search_sessions(token_hash)
                    ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_guest_sessions_expiry
            ON guest_search_sessions(expires_at)
        """)

def create_guest_consent_flow(
    conn: sqlite3.Connection,
    consent_id: int,
    event_id: str,
) -> str:
    """
    Create a temporary consent-to-selfie flow.

    Call only from the trusted consent-submission backend
    after consent has been recorded successfully.
    """

    if not verify_guest_consent(conn, consent_id, event_id):
        raise ValueError("Active selfie-search consent is required")

    token = secrets.token_urlsafe(32)
    flow_hash = hash_guest_token(token)

    now = int(time.time())
    expires_at = now + CONSENT_FLOW_DURATION_SECONDS

    with conn:
        conn.execute("""
            INSERT INTO guest_consent_flows (
                flow_hash,
                event_id,
                consent_id,
                created_at,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            flow_hash,
            event_id,
            consent_id,
            now,
            expires_at,
        ))

    return token

def verify_guest_consent(
    conn: sqlite3.Connection,
    consent_id: int,
    event_id: str,
) -> bool:
    """
    Check that a selfie-search consent exists,
    belongs to the event and has not been withdrawn.
    """

    if (
        type(consent_id) is not int
        or consent_id <= 0
        or not event_id
    ):
        return False

    result = conn.execute("""
        SELECT 1
        FROM attendee_consents
        WHERE id = ?
          AND event_id = ?
          AND consent_type = 'selfie_search'
          AND accepted_at IS NOT NULL
          AND withdrawn_at IS NULL
        LIMIT 1
    """, (consent_id, event_id)).fetchone()

    return result is not None


def validate_guest_consent_flow(
    conn: sqlite3.Connection,
    token: str,
    event_id: str,
) -> int | None:
    """
    Return the consent ID for a valid consent-flow token.
    Return None for missing, expired or withdrawn consent.
    """

    if (
        not isinstance(token, str)
        or not token
        or not isinstance(event_id, str)
        or not event_id
    ):
        return None

    flow_hash = hash_guest_token(token)
    now = int(time.time())

    row = conn.execute("""
        SELECT f.consent_id
        FROM guest_consent_flows AS f
        JOIN attendee_consents AS c
          ON c.id = f.consent_id
        WHERE f.flow_hash = ?
          AND f.event_id = ?
          AND f.expires_at > ?
          AND c.event_id = f.event_id
          AND c.consent_type = 'selfie_search'
          AND c.accepted_at IS NOT NULL
          AND c.withdrawn_at IS NULL         
        LIMIT 1
    """, (
        flow_hash,
        event_id,
        now,
    )).fetchone()

    return int(row[0]) if row else None


def create_guest_session(
    conn: sqlite3.Connection,
    event_id: str,
    consent_id: int,
    authorized_photo_ids: list[int],
) -> str:
    """Create a session from previously verified photo matches."""

    
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("Valid event ID is required")

    if type(consent_id) is not int or consent_id <= 0:
        raise ValueError("Valid consent ID is required")

    if not verify_guest_consent(conn, consent_id, event_id):
        raise ValueError(
            "Active selfie-search consent is required"
        )
    if not authorized_photo_ids:
        raise ValueError("Authorized photos are required")

    photo_ids = set(authorized_photo_ids)

    if any(
        type(photo_id) is not int or photo_id <= 0
        for photo_id in photo_ids
    ):
        raise ValueError("Invalid photo ID")

    token = secrets.token_urlsafe(32)
    token_hash = hash_guest_token(token)

    now = int(time.time())
    expires_at = now + SESSION_DURATION_SECONDS

    with conn:
        conn.execute("""
            INSERT INTO guest_search_sessions
                (token_hash, event_id, consent_id,
                 created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            token_hash,
            event_id,
            consent_id,
            now,
            expires_at,
        ))

        conn.executemany("""
            INSERT INTO guest_session_photos
                (token_hash, photo_id)
            VALUES (?, ?)
        """, [
            (token_hash, photo_id)
            for photo_id in sorted(photo_ids)
        ])

    return token


def guest_can_access_photo(
    conn: sqlite3.Connection,
    token: str,
    event_id: str,
    photo_id: int,
) -> bool:
    """Check token expiry and photo-level authorization."""

    if (
        not isinstance(token, str)
        or not token
        or not event_id
        or type(photo_id) is not int
        or photo_id <= 0
    ):
        return False

    token_hash = hash_guest_token(token)
    now = int(time.time())

    result = conn.execute("""
        SELECT 1
        FROM guest_search_sessions AS s
        JOIN guest_session_photos AS p
          ON p.token_hash = s.token_hash
        JOIN attendee_consents AS c
          ON c.id = CAST(s.consent_id AS INTEGER)
        JOIN photos AS photo
          ON photo.id = p.photo_id
         AND photo.event_id = s.event_id
        JOIN photo_face_authorizations AS auth
          ON auth.photo_id = photo.id

        WHERE s.token_hash = ?
          AND s.event_id = ?
          AND s.expires_at > ?
          AND p.photo_id = ?
          AND c.event_id = s.event_id
          AND c.consent_type = 'selfie_search'
          AND c.accepted_at IS NOT NULL
          AND c.withdrawn_at IS NULL
          AND photo.status = 'available'
          AND photo.s3_bucket = 'tam-smart-event-photos'
          AND photo.s3_key IS NOT NULL
          AND auth.authorized = 1
          AND auth.consent_reference IS NOT NULL
          AND TRIM(auth.consent_reference) != ''
        LIMIT 1                                     
    """, (
        token_hash,
        event_id,
        now,
        photo_id,
    )).fetchone()

    return result is not None


def revoke_guest_session(
    conn: sqlite3.Connection,
    token: str,
):
    """Immediately revoke a guest search session."""

    if not isinstance(token, str) or not token:
        return

    token_hash = hash_guest_token(token)

    with conn:
        conn.execute("""
            DELETE FROM guest_session_photos
            WHERE token_hash = ?
        """, (token_hash,))

        conn.execute("""
            DELETE FROM guest_search_sessions
            WHERE token_hash = ?
        """, (token_hash,))


def revoke_guest_consent_flow(
    conn: sqlite3.Connection,
    token: str,
) -> None:
    """
    Revoke a temporary consent flow.
    Does not delete the attendee's consent record.
    """

    if not isinstance(token, str) or not token:
        return

    flow_hash = hash_guest_token(token)

    with conn:
        conn.execute(
            """
            DELETE FROM guest_consent_flows
            WHERE flow_hash = ?
            """,
            (flow_hash,)
        )

def revoke_previous_guest_photo_session(
    conn: sqlite3.Connection,
    token: str,
) -> None:
    """
    Revoke an existing guest photo session.
    Reuses the current session revocation function.
    """
    if not isinstance(token, str) or not token:
        return

    revoke_guest_session(conn, token)



def consume_guest_consent_flow(
    conn: sqlite3.Connection,
    token: str,
    event_id: str,
) -> int | None:
    """
    Atomically consume a valid consent-flow token.
    A consumed token cannot be reused.
    """

    if not isinstance(token, str) or not token:
        return None

    flow_hash = hash_guest_token(token)
    now = int(time.time())

    # Use one write transaction to prevent concurrent reuse.
    conn.execute("BEGIN IMMEDIATE")

    try:
        row = conn.execute("""
            SELECT f.consent_id
            FROM guest_consent_flows AS f
            JOIN attendee_consents AS c
              ON c.id = f.consent_id
            WHERE f.flow_hash = ?
              AND f.event_id = ?
              AND f.expires_at > ?
              AND c.event_id = f.event_id
              AND c.consent_type = 'selfie_search'
              AND c.accepted_at IS NOT NULL
              AND c.withdrawn_at IS NULL
        """, (
            flow_hash,
            event_id,
            now,
        )).fetchone()

        if row is None:
            conn.rollback()
            return None

        conn.execute("""
            DELETE FROM guest_consent_flows
            WHERE flow_hash = ?
        """, (flow_hash,))

        conn.commit()
        return int(row[0])

    except Exception:
        conn.rollback()
        raise



def list_guest_authorized_photos(
    conn: sqlite3.Connection,
    token: str,
    event_id: str,
) -> list[dict]:
    """
    List photos authorized for a temporary guest session.

    Does not expose S3 locations or original image data.
    """

    if (
        not isinstance(token, str)
        or not token
        or not isinstance(event_id, str)
        or not event_id
    ):
        return []

    token_hash = hash_guest_token(token)
    now = int(time.time())

    rows = conn.execute("""
        SELECT DISTINCT
            p.id,
            c.name AS contributor_name
        FROM guest_search_sessions AS s
        JOIN guest_session_photos AS gp
          ON gp.token_hash = s.token_hash
        JOIN attendee_consents AS ac
          ON ac.id = CAST(s.consent_id AS INTEGER)
        JOIN photos AS p
          ON p.id = gp.photo_id
         AND p.event_id = s.event_id
        JOIN contributors AS c
          ON c.id = p.contributor_id
        JOIN photo_face_authorizations AS auth
          ON auth.photo_id = p.id
        WHERE s.token_hash = ?
          AND s.event_id = ?
          AND s.expires_at > ?
          AND ac.event_id = s.event_id
          AND ac.consent_type = 'selfie_search'
          AND ac.accepted_at IS NOT NULL
          AND ac.withdrawn_at IS NULL
          AND p.status = 'available'
          AND p.s3_bucket = 'tam-smart-event-photos'
          AND p.s3_key IS NOT NULL
          AND auth.authorized = 1
          AND auth.consent_reference IS NOT NULL
          AND TRIM(auth.consent_reference) != ''
        ORDER BY p.id DESC
        LIMIT 200
    """, (
        token_hash,
        event_id,
        now,
    )).fetchall()

    return [
        {
            "photo_id": row[0],
            "contributor_name": row[1]
        }
        for row in rows
    ]
