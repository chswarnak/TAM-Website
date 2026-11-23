"""
TAM Gallery registration rate limiting.

Limit registration attempts using SQLite.
Store hashed client identifiers instead of raw IP addresses.
"""

import hashlib
import hmac
import sqlite3
import time


MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60


def check_gallery_registration_limit(
    db_path: str,
    client_identifier: str,
    secret: str,
) -> bool:
    """
    Record an attempt if permitted.

    Return True if allowed, False if rate limited.
    """

    if not client_identifier or not secret:
        return False

    identifier_hash = hmac.new(
        secret.encode("utf-8"),
        client_identifier.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    now = int(time.time())
    cutoff = now - WINDOW_SECONDS

    connection = sqlite3.connect(db_path, timeout=15)

    try:
        connection.execute("BEGIN IMMEDIATE")

        connection.execute(
            """
            DELETE FROM gallery_registration_attempts
            WHERE attempted_at < ?
            """,
            (cutoff,),
        )

        attempt_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM gallery_registration_attempts
            WHERE client_identifier = ?
              AND attempted_at >= ?
            """,
            (identifier_hash, cutoff),
        ).fetchone()[0]

        if attempt_count >= MAX_ATTEMPTS:
            connection.commit()
            return False

        connection.execute(
            """
            INSERT INTO gallery_registration_attempts (
                client_identifier,
                attempted_at
            )
            VALUES (?, ?)
            """,
            (identifier_hash, now),
        )

        connection.commit()
        return True

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
