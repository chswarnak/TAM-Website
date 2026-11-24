"""Selfie-search result sessions for logged-in attendees.

A search stores the matched photo ids under a random token (kept in the
attendee's signed session cookie; only its hash is stored here). Every
photo access re-checks, in one query:
  * the token belongs to THIS attendee and has not expired;
  * the attendee's selfie-search consent is still active (not withdrawn);
  * the photo is available, in TAM's bucket and authorized for indexing.
"""

import hashlib
import secrets
import sqlite3
import time

SESSION_DURATION_SECONDS = 30 * 60
SESSION_TOKEN_KEY = "photo_search_token"

_ACCESS_SQL = """
    FROM guest_search_sessions AS s
    JOIN guest_session_photos AS gp ON gp.token_hash = s.token_hash
    JOIN attendee_consents AS ac ON ac.id = CAST(s.consent_id AS INTEGER)
    JOIN photos AS p ON p.id = gp.photo_id AND p.event_id = s.event_id
    JOIN contributors AS c ON c.id = p.contributor_id
    JOIN photo_face_authorizations AS auth ON auth.photo_id = p.id
    WHERE s.token_hash = ?
      AND s.event_id = ?
      AND s.attendee_id = ?
      AND s.expires_at > ?
      AND ac.event_id = s.event_id
      AND ac.attendee_id = s.attendee_id
      AND ac.consent_type = 'selfie_search'
      AND ac.accepted_at IS NOT NULL
      AND ac.withdrawn_at IS NULL
      AND p.status = 'available'
      AND p.s3_bucket = 'tam-smart-event-photos'
      AND p.s3_key IS NOT NULL
      AND auth.authorized = 1
      AND auth.consent_reference IS NOT NULL
      AND TRIM(auth.consent_reference) != ''
"""


def hash_guest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_search_session(conn: sqlite3.Connection, event_id: str,
                          attendee_id: int, consent_id: int,
                          photo_ids: list[int]) -> str:
    """Store a search result for one attendee and return its token."""
    if not photo_ids:
        raise ValueError("Matched photos are required")
    token = secrets.token_urlsafe(32)
    token_hash = hash_guest_token(token)
    now = int(time.time())
    with conn:
        conn.execute(
            """
            INSERT INTO guest_search_sessions
                (token_hash, event_id, consent_id, attendee_id,
                 created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (token_hash, event_id, str(consent_id), attendee_id,
             now, now + SESSION_DURATION_SECONDS)
        )
        conn.executemany(
            "INSERT INTO guest_session_photos (token_hash, photo_id) VALUES (?, ?)",
            [(token_hash, photo_id) for photo_id in sorted(set(photo_ids))]
        )
    return token


def revoke_attendee_sessions(conn: sqlite3.Connection, attendee_id: int) -> None:
    """Delete every search session belonging to this attendee."""
    with conn:
        conn.execute(
            """
            DELETE FROM guest_session_photos WHERE token_hash IN (
                SELECT token_hash FROM guest_search_sessions WHERE attendee_id = ?
            )
            """,
            (attendee_id,)
        )
        conn.execute("DELETE FROM guest_search_sessions WHERE attendee_id = ?",
                     (attendee_id,))


def accessible_photo(conn: sqlite3.Connection, token: str | None, event_id: str,
                     attendee_id: int, photo_id: int) -> sqlite3.Row | None:
    """The photo (with S3 location and contributor name) if this attendee's
    current search session may access it, else None."""
    if not token:
        return None
    conn.row_factory = sqlite3.Row
    return conn.execute(
        f"""
        SELECT p.id, p.s3_bucket, p.s3_key, c.name AS contributor_name,
               s.consent_id, s.token_hash
        {_ACCESS_SQL}
          AND gp.photo_id = ?
        LIMIT 1
        """,
        (hash_guest_token(token), event_id, attendee_id, int(time.time()), photo_id)
    ).fetchone()


def list_accessible_photos(conn: sqlite3.Connection, token: str | None,
                           event_id: str, attendee_id: int) -> list[dict]:
    """Photos in this attendee's current search result (no S3 locations)."""
    if not token:
        return []
    rows = conn.execute(
        f"""
        SELECT DISTINCT p.id, c.name AS contributor_name
        {_ACCESS_SQL}
        ORDER BY p.id DESC
        LIMIT 200
        """,
        (hash_guest_token(token), event_id, attendee_id, int(time.time()))
    ).fetchall()
    return [{"photo_id": row[0], "contributor_name": row[1]} for row in rows]
