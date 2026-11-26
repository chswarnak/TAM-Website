"""
TAM Smart Event Photos — Controlled Indexing Worker

Processes one authorized pending photo per execution.
Uses the existing production Rekognition collection.
Does not authorize photographs automatically.
"""

import sqlite3

from rekognition_service import (
    DB_PATH,
    TAM_COLLECTION_ID,
    process_photo_index_job,
)

EVENT_ID = "tam-diwali-2026"
BUCKET = "tam-smart-event-photos"


def get_next_pending_photo():
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        row = conn.execute(
            """
            SELECT j.photo_id
            FROM photo_index_jobs j
            JOIN photos p
              ON p.id = j.photo_id
            JOIN photo_face_authorizations a
              ON a.photo_id = p.id
            WHERE j.collection_id = ?
              AND j.status = 'pending'
              AND p.event_id = ?
              AND p.status = 'available'
              AND p.s3_bucket = ?
              AND p.s3_key IS NOT NULL
              AND a.authorized = 1
              AND a.consent_reference IS NOT NULL
              AND TRIM(a.consent_reference) != ''
            ORDER BY j.id
            LIMIT 1
            """,
            (TAM_COLLECTION_ID, EVENT_ID, BUCKET),
        ).fetchone()

    return row[0] if row else None


def main():
    photo_id = get_next_pending_photo()

    if photo_id is None:
        print("No authorized pending indexing jobs.")
        return

    print(f"Processing photo {photo_id}", flush=True)

    result = process_photo_index_job(photo_id)

    print(
        f"Photo {photo_id}: "
        f"status={result['status']} "
        f"faces={result['face_count']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
