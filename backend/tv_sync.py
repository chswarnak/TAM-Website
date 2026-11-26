
"""Centralized TAM Live TV S3 photograph source."""

import sqlite3
import logging
import threading

from aws_config import get_tam_s3_client
from tv_quality import screen_photo

EVENT_ID = "tam-diwali-2026"
BUCKET = "tam-smart-event-photos"

MAX_IMAGE_BYTES = 100 * 1024 * 1024
MAX_SYNC_BATCH = 4

logger = logging.getLogger("tam.live_tv")

sync_lock = threading.Lock()


def get_recent_photos(db_path, limit=30):

    limit = max(1, min(int(limit), 100))

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT
                p.id,
                p.contributor_id,
                p.s3_bucket,
                p.s3_key,
                p.status,
                q.projection_authorized,
                q.quality_status,
                q.queue_status
            FROM photos p
            LEFT JOIN tv_photo_queue q
              ON q.photo_id = p.id
             AND q.event_id = p.event_id
            WHERE p.event_id = ?
              AND p.status = 'available'
              AND p.s3_bucket = ?
              AND p.s3_key LIKE ?
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (
                EVENT_ID,
                BUCKET,
                EVENT_ID + "/%",
                limit
            )
        ).fetchall()

    return [dict(row) for row in rows]


def get_pending_photos(db_path):

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """
            SELECT
                p.id,
                p.s3_bucket,
                p.s3_key
            FROM photos p
            JOIN tv_photo_queue q
              ON q.photo_id = p.id
             AND q.event_id = p.event_id
            WHERE p.event_id = ?
              AND p.status = 'available'
              AND p.s3_bucket = ?
              AND q.projection_authorized = 1
              AND q.quality_status = 'pending'
              AND q.queue_status = 'pending'
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (
                EVENT_ID,
                BUCKET,
                MAX_SYNC_BATCH
            )
        ).fetchall()

    return [dict(row) for row in rows]


def read_s3_photo(row, client):

    bucket = row["s3_bucket"]
    key = row["s3_key"]

    if bucket != BUCKET:
        raise ValueError("Invalid S3 bucket")

    if not isinstance(key, str):
        raise ValueError("Invalid S3 key")

    if not key.startswith(EVENT_ID + "/"):
        raise ValueError("Invalid event photograph")

    if ".." in key.split("/"):
        raise ValueError("Invalid photograph path")

    if not key.lower().endswith(
        (".jpg", ".jpeg", ".png", ".webp")
    ):
        raise ValueError("Unsupported photograph format")

    response = client.get_object(
        Bucket=bucket,
        Key=key
    )

    body = response["Body"]

    try:

        if response.get("ContentLength", 0) > MAX_IMAGE_BYTES:
            raise ValueError("Photograph too large")

        data = body.read(MAX_IMAGE_BYTES + 1)

    finally:

        body.close()

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Photograph too large")

    return data


def update_screening_result(db_path, photo_id, result):

    passed = bool(result["passed"])

    with sqlite3.connect(db_path, timeout=15) as conn:

        cursor = conn.execute(
            """
            UPDATE tv_photo_queue
            SET quality_status = ?,
                queue_status = ?,
                quality_score = ?,
                quality_reason = ?
            WHERE event_id = ?
              AND photo_id = ?
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
                "passed" if passed else "rejected",
                "ready" if passed else "rejected",
                result.get("score", 0),
                str(result.get("reason", "")),
                EVENT_ID,
                photo_id
            )
        )

        conn.commit()

    return cursor.rowcount == 1


def synchronize_s3_photos(db_path):

    if not sync_lock.acquire(blocking=False):

        return {
            "busy": True,
            "processed": 0,
            "passed": 0,
            "rejected": 0,
            "failed": 0
        }

    stats = {
        "busy": False,
        "processed": 0,
        "passed": 0,
        "rejected": 0,
        "failed": 0
    }

    try:

        pending = get_pending_photos(db_path)

        if not pending:
            return stats

        client = get_tam_s3_client()

        for row in pending:

            try:

                data = read_s3_photo(row, client)

                result = screen_photo(data)

                if update_screening_result(
                    db_path,
                    row["id"],
                    result
                ):

                    stats["processed"] += 1

                    if result["passed"]:
                        stats["passed"] += 1

                    else:
                        stats["rejected"] += 1

            except Exception as error:

                stats["failed"] += 1

                logger.warning(
                    "S3 projection screening failed for photo %s: %s",
                    row["id"],
                    type(error).__name__
                )

        return stats

    finally:

        sync_lock.release()


# TAM-RECENT-PHOTO-REGISTRATION-V1

def register_authorized_recent_photos(
    db_path,
    authorized_ids
):

    ids = list(dict.fromkeys(
        int(photo_id)
        for photo_id in authorized_ids
        if int(photo_id) > 0
    ))

    registered = 0

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        for photo_id in ids:

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
                SELECT
                    p.event_id,
                    p.id,
                    1,
                    'pending',
                    'pending',
                    CURRENT_TIMESTAMP
                FROM photos p
                WHERE p.id = ?
                  AND p.event_id = ?
                  AND p.status = 'available'
                  AND p.s3_bucket = ?
                  AND p.s3_key LIKE ?
                  AND (
                      LOWER(p.s3_key) LIKE '%.jpg'
                      OR LOWER(p.s3_key) LIKE '%.jpeg'
                      OR LOWER(p.s3_key) LIKE '%.png'
                      OR LOWER(p.s3_key) LIKE '%.webp'
                  )
                """,
                (
                    photo_id,
                    EVENT_ID,
                    BUCKET,
                    EVENT_ID + "/%"
                )
            )

            registered += cursor.rowcount

        conn.commit()

    return registered


# TAM-AUTOMATIC-S3-SYNC-V2

import time
from threading import Thread, Lock

_scheduler_lock = Lock()

_scheduler_running = False

_last_sync = 0.0

SYNC_INTERVAL = 2


def register_event_policy_photos(db_path):

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.execute("BEGIN IMMEDIATE")

        policy = conn.execute(
            """
            SELECT automatic_enabled, consent_reference
            FROM tv_projection_policy
            WHERE event_id = ?
            """,
            (EVENT_ID,)
        ).fetchone()

        if (
            not policy
            or policy[0] != 1
            or not policy[1]
        ):
            return 0

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
            SELECT
                p.event_id,
                p.id,
                1,
                'pending',
                'pending',
                CURRENT_TIMESTAMP
            FROM photos p
            WHERE p.event_id = ?
              AND p.status = 'available'
              AND p.s3_bucket = ?
              AND p.s3_key LIKE ?
              AND (
                  LOWER(p.s3_key) LIKE '%.jpg'
                  OR LOWER(p.s3_key) LIKE '%.jpeg'
                  OR LOWER(p.s3_key) LIKE '%.png'
                  OR LOWER(p.s3_key) LIKE '%.webp'
              )
            ORDER BY p.id DESC
            """,
            (
                EVENT_ID,
                BUCKET,
                EVENT_ID + "/%"
            )
        )

        registered = cursor.rowcount

        conn.commit()

        return registered


def automatic_sync_worker(db_path):

    global _scheduler_running
    global _last_sync

    try:

        with sqlite3.connect(
            db_path, timeout=15
        ) as conn:

            enabled = conn.execute(
                """
                SELECT enabled
                FROM tv_event_settings
                WHERE event_id = ?
                """,
                (EVENT_ID,)
            ).fetchone()

        if not enabled or enabled[0] != 1:
            return

        register_event_policy_photos(db_path)

        synchronize_s3_photos(db_path)

    except Exception as error:

        logger.warning(
            "Automatic TV synchronization failed: %s",
            type(error).__name__
        )

    finally:

        with _scheduler_lock:

            _last_sync = time.monotonic()

            _scheduler_running = False


def schedule_automatic_sync(db_path):

    global _scheduler_running

    with _scheduler_lock:

        if _scheduler_running:
            return False

        if time.monotonic() - _last_sync < SYNC_INTERVAL:
            return False

        _scheduler_running = True

    try:

        Thread(
            target=automatic_sync_worker,
            args=(db_path,),
            daemon=True,
            name="tam-tv-s3-sync"
        ).start()

    except Exception:

        with _scheduler_lock:
            _scheduler_running = False

        raise

    return True
