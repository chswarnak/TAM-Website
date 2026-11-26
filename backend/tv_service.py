
"""TAM Live TV adaptive photo queue."""

import sqlite3
import json
from datetime import datetime, timezone, timedelta

EVENT_ID = "tam-diwali-2026"

MAX_BATCH = 4
BATCH_SECONDS = 10


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.isoformat(timespec="seconds")


def get_next_batch(db_path, event_id=EVENT_ID, now=None):

    now = now or utc_now()

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        settings = conn.execute(
            """
            SELECT *
            FROM tv_event_settings
            WHERE event_id = ?
            """,
            (event_id,)
        ).fetchone()

        state = conn.execute(
            """
            SELECT *
            FROM tv_display_state
            WHERE event_id = ?
            """,
            (event_id,)
        ).fetchone()

        if not settings or not state:
            return {"mode": "blank", "photos": []}

        if settings["enabled"] != 1:
            return {"mode": "blank", "photos": []}

        # TAM-TV-MANUAL-MODES-V2

        mode = state["display_mode"]

        if mode in ("blank", "qr", "paused"):
            return {"mode": mode, "photos": []}

        if mode == "slideshow":
            return {"mode": "slideshow", "photos": []}

        if mode == "auto" and settings["auto_cycle_enabled"] != 1:
            return {"mode": "slideshow", "photos": []}

        # Never display deleted or unauthorized photos.

        current_ids = json.loads(
            state["current_batch_json"] or "[]"
        )

        eligible_current = []

        for photo_id in current_ids[:MAX_BATCH]:

            row = conn.execute(
                """
                SELECT p.id
                FROM photos p
                JOIN tv_photo_queue q
                  ON q.photo_id = p.id
                WHERE p.id = ?
                  AND p.event_id = ?
                  AND q.event_id = p.event_id
                  AND p.status = 'available'
                  AND q.projection_authorized = 1
                  AND q.quality_status IN (
                      'passed', 'manual_approved'
                  )
                  AND q.queue_status = 'displaying'
                """,
                (photo_id, event_id)
            ).fetchone()

            if row:
                eligible_current.append(photo_id)

        batch_end = state["batch_ends_at_utc"]

        if batch_end and current_ids:

            expiry = datetime.fromisoformat(batch_end)

            if now < expiry:

                return {
                    "mode": "photo_wall",
                    "photos": eligible_current,
                    "ends_at": batch_end
                }

        # Finish the previous batch.

        for photo_id in current_ids:

            conn.execute(
                """
                UPDATE tv_photo_queue
                SET queue_status = 'displayed',
                    displayed_at_utc = ?
                WHERE photo_id = ?
                  AND event_id = ?
                  AND queue_status = 'displaying'
                """,
                (timestamp(now), photo_id, event_id)
            )

        limit = min(
            MAX_BATCH,
            max(1, settings["max_photos_per_batch"])
        )

        # Oldest eligible photo IDs first.

        rows = conn.execute(
            """
            SELECT q.photo_id
            FROM tv_photo_queue q
            JOIN photos p ON p.id = q.photo_id
            WHERE q.event_id = ?
              AND p.event_id = q.event_id
              AND p.status = 'available'
              AND q.projection_authorized = 1
              AND q.quality_status IN (
                  'passed', 'manual_approved'
              )
              AND q.queue_status = 'ready'
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (event_id, limit)
        ).fetchall()

        photo_ids = [row["photo_id"] for row in rows]

        for photo_id in photo_ids:

            conn.execute(
                """
                UPDATE tv_photo_queue
                SET queue_status = 'displaying'
                WHERE photo_id = ?
                  AND event_id = ?
                  AND queue_status = 'ready'
                """,
                (photo_id, event_id)
            )

        ends_at = timestamp(
            now + timedelta(seconds=BATCH_SECONDS)
        ) if photo_ids else None

        conn.execute(
            """
            UPDATE tv_display_state
            SET current_batch_json = ?,
                batch_started_at_utc = ?,
                batch_ends_at_utc = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE event_id = ?
            """,
            (
                json.dumps(photo_ids),
                timestamp(now) if photo_ids else None,
                ends_at,
                event_id
            )
        )

        conn.commit()

        if not photo_ids and mode == "photo_wall":

            replay = conn.execute(
                """
                SELECT p.id
                FROM photos p
                JOIN tv_photo_queue q
                  ON q.photo_id = p.id
                WHERE p.event_id = ?
                  AND q.event_id = p.event_id
                  AND p.status = 'available'
                  AND q.projection_authorized = 1
                  AND q.quality_status IN (
                      'passed', 'manual_approved'
                  )
                  AND q.queue_status = 'displayed'
                ORDER BY p.id DESC
                LIMIT ?
                """,
                (event_id, limit)
            ).fetchall()

            return {
                "mode": "photo_wall",
                "photos": [row[0] for row in replay],
                "ends_at": None
            }

        return {
            "mode": "photo_wall" if photo_ids else "slideshow",
            "photos": photo_ids,
            "ends_at": ends_at
        }


def get_idle_photos(db_path, event_id=EVENT_ID):

    with sqlite3.connect(db_path) as conn:

        rows = conn.execute(
            """
            SELECT p.id
            FROM photos p
            JOIN tv_photo_queue q
              ON q.photo_id = p.id
            JOIN tv_event_settings s
              ON s.event_id = p.event_id
            WHERE p.event_id = ?
              AND q.event_id = p.event_id
              AND s.enabled = 1
              AND p.status = 'available'
              AND q.projection_authorized = 1
              AND q.quality_status IN (
                  'passed', 'manual_approved'
              )
              AND (
                  q.queue_status = 'displayed'
                  OR (
                      q.queue_status IN (
                          'ready', 'displaying'
                      )
                      AND EXISTS (
                          SELECT 1
                          FROM tv_display_state d
                          WHERE d.event_id = p.event_id
                            AND (
                                d.display_mode = 'slideshow'
                                OR (
                                    d.display_mode = 'auto'
                                    AND s.auto_cycle_enabled = 0
                                )
                            )
                      )
                  )
              )
            ORDER BY p.id
            """,
            (event_id,)
        ).fetchall()

        return [row[0] for row in rows]
