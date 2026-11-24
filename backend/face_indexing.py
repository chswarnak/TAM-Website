"""Who authorizes a photo's faces for indexing, and when indexing runs.

Per-event switch events.auto_authorize_face_indexing:
  * on  -> every upload is authorized under the event's facial-indexing
           policy and indexed right after upload;
  * off -> uploads wait until an admin with can_manage_photos authorizes
           them on /admin/photos/face-indexing.
"""

import logging
import sqlite3

from botocore.exceptions import BotoCoreError, ClientError

import rekognition_service
from consent_service import CONSENT_VERSION
from database import DB_PATH
from settings import SETTINGS

logger = logging.getLogger(__name__)

INDEXING_ERRORS = (ClientError, BotoCoreError, PermissionError, sqlite3.Error,
                   rekognition_service.FaceCollectionMissingError)


def event_policy_reference() -> str:
    return f"event-policy:{SETTINGS.event_id}:facial_indexing:v{CONSENT_VERSION}"


def auto_authorize_enabled() -> bool:
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        row = connection.execute(
            "SELECT auto_authorize_face_indexing FROM events WHERE event_id = ?",
            (SETTINGS.event_id,)
        ).fetchone()
    return bool(row and row[0])


def _authorize_and_index(photo_id: int, consent_reference: str) -> None:
    rekognition_service.authorize_photo_for_indexing(photo_id, consent_reference)
    try:
        rekognition_service.index_photo_now(photo_id)
    except INDEXING_ERRORS:
        # process_photo_index_job already logged the cause and marked the
        # job failed; the photo stays retryable from the admin page.
        logger.warning("Photo %s was authorized but indexing failed", photo_id)


def index_new_upload(photo_id: int) -> None:
    """Background task run after every successful upload."""
    if auto_authorize_enabled():
        _authorize_and_index(photo_id, event_policy_reference())
    else:
        logger.info("Photo %s awaits admin face-indexing authorization", photo_id)


def authorize_by_admin(photo_ids: list[int], admin_id: int) -> None:
    reference = f"admin:{admin_id}:{event_policy_reference()}"
    for photo_id in photo_ids:
        _authorize_and_index(photo_id, reference)


def photos_awaiting_indexing() -> list[dict]:
    """Available photos that are not authorized yet, or whose job failed."""
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT p.id, p.uploaded_at, c.name AS contributor_name,
                   COALESCE(a.authorized, 0) AS authorized,
                   j.status AS job_status, j.error_message
            FROM photos p
            JOIN contributors c ON c.id = p.contributor_id
            LEFT JOIN photo_face_authorizations a ON a.photo_id = p.id
            LEFT JOIN photo_index_jobs j
              ON j.photo_id = p.id AND j.collection_id = ?
            WHERE p.event_id = ? AND p.status = 'available'
              AND p.s3_key IS NOT NULL
              AND (COALESCE(a.authorized, 0) = 0 OR j.status IS NULL
                   OR j.status = 'failed')
            ORDER BY p.id
            """,
            (SETTINGS.collection_id, SETTINGS.event_id)
        ).fetchall()
    return [dict(row) for row in rows]
