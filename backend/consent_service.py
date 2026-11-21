
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from database import DB_PATH

EVENT_ID = "tam-diwali-2026"
CONSENT_VERSION = "1.0"

CONSENT_FORM_STATUS = "development"

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
    attendee_name,
    consent_type,
    attendee_mobile=None
):
    """
    Record an explicitly submitted attendee consent.
    Preserve existing records and consent functionality.
    """

    name = " ".join(attendee_name.strip().split())

    if not name or len(name) > 150:
        raise ValueError("Please enter a valid attendee name.")

    if consent_type not in CONSENT_TEXTS:
        raise ValueError("Invalid consent type.")

    mobile = str(attendee_mobile or "").strip()

    # Accept common Canadian and international phone formats.
    import re

    if mobile:
        if not re.fullmatch(r"\+?[0-9 ()-]{7,20}", mobile):
            raise ValueError("Please enter a valid mobile number.")

        digits = re.sub(r"\D", "", mobile)

        if not 7 <= len(digits) <= 15:
            raise ValueError("Please enter a valid mobile number.")

    accepted_utc = datetime.now(timezone.utc)
    accepted_local = accepted_utc.astimezone(MONTREAL_TIMEZONE)

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        cursor = connection.execute(
            """
            INSERT INTO attendee_consents (
                event_id,
                attendee_name,
                attendee_mobile,
                consent_type,
                consent_version,
                consent_text,
                accepted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                EVENT_ID,
                name,
                mobile or None,
                consent_type,
                CONSENT_VERSION,
                CONSENT_TEXTS[consent_type],
                accepted_utc.isoformat()
            )
        )

        consent_id = cursor.lastrowid
        connection.commit()

    return {
        "consent_id": consent_id,
        "attendee_name": name,
        "consent_type": consent_type,
        "accepted_at_utc": accepted_utc.isoformat(),
        "accepted_at_montreal": accepted_local.isoformat(),
        "consent_version": CONSENT_VERSION
    }

