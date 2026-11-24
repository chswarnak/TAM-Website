
"""Biometric consent records for attendee accounts."""

import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from database import DB_PATH
from settings import SETTINGS

EVENT_ID = SETTINGS.event_id
CONSENT_VERSION = "1.0"

# Replace these values with approved TAM privacy details
# before activating consent collection.
PRIVACY_CONTACT = ""
BIOMETRIC_RETENTION_POLICY = ""
CONSENT_RECORD_RETENTION_POLICY = ""

MONTREAL_TIMEZONE = ZoneInfo("America/Toronto")

CONSENT_TEXTS = {
    "facial_indexing": (
        "I voluntarily agree to TAM using AWS Rekognition "
        "to create and store facial characteristics from "
        "authorized TAM event photographs containing me, "
        "for the purpose of helping me find my photographs. "
        "I understand that participation is optional and "
        "that I may request withdrawal and deletion."
    ),
    "selfie_search": (
        "I voluntarily agree to TAM processing the selfie "
        "I submit using AWS Rekognition to search authorized "
        "TAM event photographs. I understand that my selfie "
        "will not be enrolled in the facial collection, "
        "participation is optional, and I may decline."
    )
}



def record_attendee_consent(
    attendee_id: int,
    attendee_name: str,
    consent_type: str,
    attendee_mobile: str | None = None,
) -> dict:
    """Record a consent given by a logged-in attendee account."""
    if consent_type not in CONSENT_TEXTS:
        raise ValueError("Invalid consent type.")

    accepted_utc = datetime.now(timezone.utc)
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        cursor = connection.execute(
            """
            INSERT INTO attendee_consents (
                event_id, attendee_id, attendee_name, attendee_mobile,
                consent_type, consent_version, consent_text, accepted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (EVENT_ID, attendee_id, attendee_name, attendee_mobile,
             consent_type, CONSENT_VERSION, CONSENT_TEXTS[consent_type],
             accepted_utc.isoformat())
        )

    return {
        "consent_id": cursor.lastrowid,
        "consent_type": consent_type,
        "accepted_at_utc": accepted_utc.isoformat(),
        "accepted_at_montreal": accepted_utc.astimezone(MONTREAL_TIMEZONE).isoformat(),
        "consent_version": CONSENT_VERSION,
    }


def withdraw_consents(attendee_id: int) -> int:
    """Withdraw every active consent of this attendee. Returns the count."""
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        cursor = connection.execute(
            """
            UPDATE attendee_consents
            SET withdrawn_at = ?
            WHERE event_id = ? AND attendee_id = ? AND withdrawn_at IS NULL
            """,
            (datetime.now(timezone.utc).isoformat(), EVENT_ID, attendee_id)
        )
        return cursor.rowcount


def active_consent_id(attendee_id: int, consent_type: str) -> int | None:
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        row = connection.execute(
            """
            SELECT id FROM attendee_consents
            WHERE event_id = ? AND attendee_id = ? AND consent_type = ?
              AND accepted_at IS NOT NULL AND withdrawn_at IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (EVENT_ID, attendee_id, consent_type)
        ).fetchone()
    return row[0] if row else None
