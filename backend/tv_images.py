
"""TAM Live TV secure projection image delivery."""

import sqlite3
import threading

from collections import OrderedDict

from tv_quality import create_projection_image

ALLOWED_BUCKET = "tam-smart-event-photos"
EVENT_ID = "tam-diwali-2026"

MAX_SOURCE_BYTES = 100 * 1024 * 1024

MAX_CACHE_BYTES = 32 * 1024 * 1024
MAX_CACHE_ITEMS = 40

_cache = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()

# Limit simultaneous large-image processing.
_processing_limit = threading.BoundedSemaphore(2)


class ProjectionImageError(Exception):
    pass


def get_authorized_photo(
    db_path,
    photo_id,
    event_id=EVENT_ID
):

    with sqlite3.connect(db_path, timeout=10) as conn:

        conn.row_factory = sqlite3.Row

        row = conn.execute(
            """
            SELECT
                p.id,
                p.s3_bucket,
                p.s3_key
            FROM photos p
            JOIN tv_photo_queue q
              ON q.photo_id = p.id
            JOIN tv_event_settings s
              ON s.event_id = p.event_id
            JOIN tv_display_state d
              ON d.event_id = p.event_id
            WHERE p.id = ?
              AND p.event_id = ?
              AND q.event_id = p.event_id
              AND s.enabled = 1
              AND d.display_mode IN (
                  'auto', 'slideshow', 'photo_wall'
              )
              AND p.status = 'available'
              AND q.projection_authorized = 1
              AND q.quality_status IN (
                  'passed', 'manual_approved'
              )
              AND (
                  q.queue_status IN (
                      'displaying', 'displayed'
                  )
                  OR (
                      q.queue_status = 'ready'
                      AND (
                          d.display_mode = 'slideshow'
                          OR (
                              d.display_mode = 'auto'
                              AND s.auto_cycle_enabled = 0
                          )
                      )
                  )
              )
            """,
            (photo_id, event_id)
        ).fetchone()

    if not row:
        raise ProjectionImageError(
            "Photograph unavailable for projection"
        )

    bucket = row["s3_bucket"]
    key = row["s3_key"]

    if bucket != ALLOWED_BUCKET:
        raise ProjectionImageError(
            "Invalid projection storage"
        )

    if (
        not isinstance(key, str)
        or not key.startswith(event_id + "/")
        or key.endswith("/")
        or ".." in key.split("/")
    ):
        raise ProjectionImageError(
            "Invalid photograph location"
        )

    return bucket, key


def clear_projection_cache():

    global _cache_bytes

    with _cache_lock:

        _cache.clear()
        _cache_bytes = 0


def get_projection_image(
    db_path,
    photo_id,
    s3_client,
    event_id=EVENT_ID
):

    global _cache_bytes

    # Always authorize before checking the memory cache.

    bucket, key = get_authorized_photo(
        db_path,
        photo_id,
        event_id
    )

    cache_key = (
        event_id,
        photo_id,
        bucket,
        key
    )

    with _cache_lock:

        cached = _cache.get(cache_key)

        if cached is not None:
            _cache.move_to_end(cache_key)

    if cached is not None:

        if get_authorized_photo(
            db_path, photo_id, event_id
        ) != (bucket, key):

            raise ProjectionImageError(
                "Photograph authorization changed"
            )

        return cached

    if not _processing_limit.acquire(timeout=3):

        raise ProjectionImageError(
            "Projection image service busy"
        )

    try:

        response = s3_client.get_object(
            Bucket=bucket,
            Key=key
        )

        body = response["Body"]

        try:

            size = response.get("ContentLength", 0)

            if size > MAX_SOURCE_BYTES:

                raise ProjectionImageError(
                    "Photograph exceeds size limit"
                )

            image_bytes = body.read(
                MAX_SOURCE_BYTES + 1
            )

        finally:

            body.close()

        if len(image_bytes) > MAX_SOURCE_BYTES:

            raise ProjectionImageError(
                "Photograph exceeds size limit"
            )

        optimized = create_projection_image(
            image_bytes
        )

        # Revalidate after processing.

        latest = get_authorized_photo(
            db_path,
            photo_id,
            event_id
        )

        if latest != (bucket, key):

            raise ProjectionImageError(
                "Photograph authorization changed"
            )

        with _cache_lock:

            old = _cache.pop(cache_key, None)

            if old is not None:
                _cache_bytes -= len(old)

            if len(optimized) <= MAX_CACHE_BYTES:

                while _cache and (
                    len(_cache) >= MAX_CACHE_ITEMS
                    or _cache_bytes + len(optimized)
                       > MAX_CACHE_BYTES
                ):

                    _, removed = _cache.popitem(
                        last=False
                    )

                    _cache_bytes -= len(removed)

                _cache[cache_key] = optimized

                _cache_bytes += len(optimized)

        return optimized

    finally:

        _processing_limit.release()
