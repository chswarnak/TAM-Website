
"""TAM Event Camera to Live TV registration."""

import sqlite3
import logging

from pathlib import Path
from threading import BoundedSemaphore

from tv_quality import screen_photo

EVENT_ID = "tam-diwali-2026"
ALLOWED_BUCKET = "tam-smart-event-photos"
MAX_SOURCE_BYTES = 100 * 1024 * 1024

logger = logging.getLogger("tam.live_tv")

_screening_limit = BoundedSemaphore(1)


def register_projection_photo(
    db_path,
    photo_id,
    contributor_id,
    event_id,
    projection_authorized
):

    if projection_authorized is not True:
        return False

    if event_id != EVENT_ID:
        return False

    if type(photo_id) is not int or photo_id <= 0:
        return False

    if type(contributor_id) is not int or contributor_id <= 0:
        return False

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            """
            SELECT p.id
            FROM photos p
            JOIN contributors c
              ON c.id = p.contributor_id
            WHERE p.id = ?
              AND p.event_id = ?
              AND p.contributor_id = ?
              AND p.status = 'available'
              AND p.s3_bucket = ?
              AND c.event_id = p.event_id
              AND c.approved = 1
              AND c.active = 1
              AND c.deleted_at IS NULL
            """,
            (
                photo_id,
                event_id,
                contributor_id,
                ALLOWED_BUCKET
            )
        ).fetchone()

        if not row:
            return False

        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO tv_photo_queue (
                event_id,
                photo_id,
                projection_authorized,
                quality_status,
                queue_status,
                authorized_at_utc
            )
            VALUES (
                ?, ?, 1, 'pending', 'pending',
                CURRENT_TIMESTAMP
            )
            """,
            (event_id, photo_id)
        )

        conn.commit()

        return cursor.rowcount == 1


def process_projection_photo(
    db_path,
    photo_id,
    event_id=EVENT_ID
):

    if event_id != EVENT_ID:
        return False

    try:

        with _screening_limit:

            with sqlite3.connect(
                db_path, timeout=15
            ) as conn:

                conn.row_factory = sqlite3.Row

                row = conn.execute(
                    """
                    SELECT p.file_path
                    FROM photos p
                    JOIN tv_photo_queue q
                      ON q.photo_id = p.id
                    WHERE p.id = ?
                      AND p.event_id = ?
                      AND q.event_id = p.event_id
                      AND p.status = 'available'
                      AND q.projection_authorized = 1
                      AND q.quality_status = 'pending'
                      AND q.queue_status = 'pending'
                    """,
                    (photo_id, event_id)
                ).fetchone()

            if not row:
                return False

            path = Path(row["file_path"])

            if not path.is_file():
                raise ValueError("Local photograph unavailable")

            with path.open("rb") as file:

                image_bytes = file.read(
                    MAX_SOURCE_BYTES + 1
                )

            if len(image_bytes) > MAX_SOURCE_BYTES:

                result = {
                    "passed": False,
                    "score": 0,
                    "reason": "Photograph exceeds processing limit"
                }

            else:

                result = screen_photo(image_bytes)

            quality_status = (
                "passed" if result["passed"]
                else "rejected"
            )

            queue_status = (
                "ready" if result["passed"]
                else "rejected"
            )

            with sqlite3.connect(
                db_path, timeout=15
            ) as conn:

                conn.execute("PRAGMA foreign_keys = ON")

                cursor = conn.execute(
                    """
                    UPDATE tv_photo_queue
                    SET quality_status = ?,
                        queue_status = ?,
                        quality_score = ?,
                        quality_reason = ?
                    WHERE photo_id = ?
                      AND event_id = ?
                      AND projection_authorized = 1
                      AND quality_status = 'pending'
                      AND queue_status = 'pending'
                      AND EXISTS (
                          SELECT 1
                          FROM photos p
                          WHERE p.id = tv_photo_queue.photo_id
                            AND p.event_id = tv_photo_queue.event_id
                            AND p.status = 'available'
                      )
                    """,
                    (
                        quality_status,
                        queue_status,
                        result.get("score", 0),
                        result.get("reason", ""),
                        photo_id,
                        event_id
                    )
                )

                conn.commit()

                return cursor.rowcount == 1

    except Exception as error:

        logger.warning(
            "Projection screening failed for photo %s: %s",
            photo_id,
            type(error).__name__
        )

        # Preserve the original photo and pending queue record.
        # An unsuccessful screening must never authorize display.

        return False
