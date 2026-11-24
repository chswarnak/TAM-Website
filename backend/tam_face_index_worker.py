"""
TAM Smart Event Photo Sharing
Facial Indexing Worker — Dry Run V1

This worker does not modify the database.
It does not contact AWS.
It identifies pending jobs that already have
valid photo-authorization records.
"""

import sqlite3
from pathlib import Path

DB_FILE = Path(
    "/data/tam-website/database/smart_event.db"
)

EVENT_ID = "tam-diwali-2026"
COLLECTION_ID = "tam-diwali-2026-faces"


def inspect_pending_jobs():
    if not DB_FILE.is_file():
        raise RuntimeError(
            "Production database not found."
        )

    uri = DB_FILE.resolve().as_uri() + "?mode=ro"

    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")

        pending = connection.execute(
            """
            SELECT COUNT(DISTINCT j.photo_id)
            FROM photo_index_jobs j
            JOIN photos p
              ON p.id = j.photo_id
            JOIN photo_face_authorizations a
              ON a.photo_id = p.id
            WHERE p.event_id = ?
              AND p.status = 'available'
              AND p.s3_bucket = 'tam-smart-event-photos'
              AND p.s3_key IS NOT NULL
              AND j.collection_id = ?
              AND j.status = 'pending'
              AND a.authorized = 1
              AND a.consent_reference IS NOT NULL
              AND TRIM(a.consent_reference) != ''
            """,
            (EVENT_ID, COLLECTION_ID),
        ).fetchone()[0]

        return pending


# TAM-INDEX-QUEUE-REPORT-V1
def inspect_indexing_coverage():
    """
    Return aggregate indexing statistics.

    This function never modifies photographs,
    consent records, or indexing jobs.
    """

    if not DB_FILE.is_file():
        raise RuntimeError("Production database not found.")

    uri = DB_FILE.resolve().as_uri() + "?mode=ro"

    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("PRAGMA query_only = ON")

        query = """
            SELECT
                COUNT(*) AS total,

                SUM(CASE
                    WHEN a.photo_id IS NULL
                    THEN 1 ELSE 0
                END) AS missing_authorization,

                SUM(CASE
                    WHEN a.authorized = 0
                    THEN 1 ELSE 0
                END) AS denied,

                SUM(CASE
                    WHEN a.authorized = 1
                     AND a.consent_reference IS NOT NULL
                     AND TRIM(a.consent_reference) != ''
                    THEN 1 ELSE 0
                END) AS authorized,

                SUM(CASE
                    WHEN j.status = 'pending'
                    THEN 1 ELSE 0
                END) AS pending,

                SUM(CASE
                    WHEN j.status = 'processing'
                    THEN 1 ELSE 0
                END) AS processing,

                SUM(CASE
                    WHEN j.status = 'completed'
                    THEN 1 ELSE 0
                END) AS completed

            FROM photos p

            LEFT JOIN photo_face_authorizations a
              ON a.photo_id = p.id

            LEFT JOIN photo_index_jobs j
              ON j.photo_id = p.id
             AND j.collection_id = ?

            WHERE p.event_id = ?
              AND p.status = 'available'
        """

        row = connection.execute(
            query,
            (COLLECTION_ID, EVENT_ID),
        ).fetchone()

        return {
            "available_photos": row[0] or 0,
            "missing_authorization": row[1] or 0,
            "explicitly_denied": row[2] or 0,
            "authorized": row[3] or 0,
            "pending_jobs": row[4] or 0,
            "processing_jobs": row[5] or 0,
            "completed_jobs": row[6] or 0,
        }



def select_authorized_pending_jobs(limit=5):
    """Select authorized pending jobs without modifying data."""
    if not isinstance(limit, int) or not 1 <= limit <= 5:
        raise ValueError("Limit must be between 1 and 5.")

    uri = DB_FILE.resolve().as_uri() + "?mode=ro"

    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")

        rows = conn.execute("""
            SELECT DISTINCT j.photo_id
            FROM photo_index_jobs j
            JOIN photos p ON p.id = j.photo_id
            JOIN photo_face_authorizations a
              ON a.photo_id = p.id
            WHERE p.event_id = ?
              AND p.status = 'available'
              AND p.s3_bucket = 'tam-smart-event-photos'
              AND p.s3_key IS NOT NULL
              AND j.collection_id = ?
              AND j.status = 'pending'
              AND a.authorized = 1
              AND a.consent_reference IS NOT NULL
              AND TRIM(a.consent_reference) != ''
            ORDER BY j.photo_id
            LIMIT ?
        """, (EVENT_ID, COLLECTION_ID, limit)).fetchall()

    return [row[0] for row in rows]


if __name__ == "__main__":
    print("=== TAM FACIAL INDEXING WORKER DRY RUN ===")

    report = inspect_indexing_coverage()

    for label, count in report.items():
        print(f"{label}: {count}")

    print("Authorized pending jobs:", inspect_pending_jobs())
    print("Database: READ ONLY")
    print("AWS Rekognition: NOT CALLED")
    print("AWS S3: NOT ACCESSED")
    print("No indexing performed.")
