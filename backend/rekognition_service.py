"""Face indexing and selfie search with Amazon Rekognition.

A photo is indexed only when it has an explicit authorization row in
photo_face_authorizations (written automatically at upload when the event's
auto_authorize_face_indexing switch is on, or by an admin otherwise).
"""

import logging
import sqlite3
from io import BytesIO

from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

from aws_config import get_tam_aws_session
from database import DB_PATH
from settings import SETTINGS

logger = logging.getLogger(__name__)

TAM_REKOGNITION_REGION = "ca-central-1"
TAM_S3_BUCKET = "tam-smart-event-photos"
FACE_MATCH_THRESHOLD = 95
MAX_SELFIE_BYTES = 20 * 1024 * 1024

# Shared WHERE fragment: the photo is available, stored in TAM's bucket and
# explicitly authorized for facial indexing. Parameters: (event_id,)
_AUTHORIZED_PHOTO_SQL = """
    p.event_id = ?
    AND p.status = 'available'
    AND p.s3_bucket = 'tam-smart-event-photos'
    AND p.s3_key IS NOT NULL
    AND a.authorized = 1
    AND a.consent_reference IS NOT NULL
    AND TRIM(a.consent_reference) != ''
"""


class FaceCollectionMissingError(RuntimeError):
    """The configured Rekognition collection does not exist."""


def get_tam_rekognition_client():
    """Create a Rekognition client from TAM's configured AWS credentials."""
    return get_tam_aws_session().client(
        "rekognition",
        region_name=TAM_REKOGNITION_REGION
    )


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=10)


def authorize_photo_for_indexing(photo_id: int, consent_reference: str) -> None:
    """Record that this photo's faces may be indexed."""
    if not consent_reference.strip():
        raise ValueError("A consent reference is required.")
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO photo_face_authorizations
                (photo_id, authorized, consent_reference, authorized_at)
            VALUES (?, 1, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(photo_id) DO UPDATE SET
                authorized = 1,
                consent_reference = excluded.consent_reference,
                authorized_at = excluded.authorized_at
            """,
            (photo_id, consent_reference)
        )


def _is_missing_collection(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ResourceNotFoundException"


def _index_faces(client, bucket: str, s3_key: str, photo_id: int) -> list[str]:
    def call():
        return client.index_faces(
            CollectionId=SETTINGS.collection_id,
            Image={"S3Object": {"Bucket": bucket, "Name": s3_key}},
            ExternalImageId=f"tam_photo_{photo_id}",
            DetectionAttributes=[],
            MaxFaces=100,
            QualityFilter="AUTO"
        )

    try:
        result = call()
    except ClientError as error:
        if not _is_missing_collection(error):
            raise
        if not SETTINGS.collection_id_explicit:
            # Never create a collection implicitly for the default
            # (live) collection id; that would hide a misconfiguration.
            raise FaceCollectionMissingError(
                f"Rekognition collection {SETTINGS.collection_id!r} does not "
                "exist. Set TAM_REKOGNITION_COLLECTION_ID to create one."
            ) from error
        logger.warning(
            "CREATING Rekognition collection %r because it does not exist "
            "and TAM_REKOGNITION_COLLECTION_ID is set explicitly.",
            SETTINGS.collection_id
        )
        client.create_collection(CollectionId=SETTINGS.collection_id)
        result = call()

    return [record["Face"]["FaceId"] for record in result.get("FaceRecords", [])]


def index_event_photo(photo_id: int) -> list[str]:
    """Index one authorized photo and return the Rekognition face ids."""
    with _connect() as connection:
        photo = connection.execute(
            f"""
            SELECT p.s3_bucket, p.s3_key
            FROM photos p
            JOIN photo_face_authorizations a ON a.photo_id = p.id
            WHERE p.id = ? AND {_AUTHORIZED_PHOTO_SQL}
            """,
            (photo_id, SETTINGS.event_id)
        ).fetchone()

    if photo is None:
        raise PermissionError("Photo is not authorized for facial indexing.")

    bucket, s3_key = photo
    return _index_faces(get_tam_rekognition_client(), bucket, s3_key, photo_id)


def register_photo_index_job(photo_id: int) -> dict:
    """Queue an authorized photo for indexing (idempotent).

    A previously failed job is reset to pending so it can be retried.
    """
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        photo = connection.execute(
            f"""
            SELECT p.id
            FROM photos p
            JOIN photo_face_authorizations a ON a.photo_id = p.id
            WHERE p.id = ? AND {_AUTHORIZED_PHOTO_SQL}
            """,
            (photo_id, SETTINGS.event_id)
        ).fetchone()
        if photo is None:
            connection.rollback()
            raise PermissionError("Photo is not authorized for facial indexing.")

        connection.execute(
            """
            INSERT INTO photo_index_jobs (photo_id, collection_id, status)
            VALUES (?, ?, 'pending')
            ON CONFLICT(photo_id, collection_id) DO UPDATE SET
                status = 'pending', error_message = NULL
            WHERE photo_index_jobs.status = 'failed'
            """,
            (photo_id, SETTINGS.collection_id)
        )
        job = connection.execute(
            "SELECT id, status FROM photo_index_jobs "
            "WHERE photo_id = ? AND collection_id = ?",
            (photo_id, SETTINGS.collection_id)
        ).fetchone()

    return {"job_id": job[0], "status": job[1]}


def claim_photo_index_job(photo_id: int) -> bool:
    """Atomically move a pending job to processing. True if claimed."""
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            f"""
            UPDATE photo_index_jobs
            SET status = 'processing',
                attempt_count = attempt_count + 1,
                started_at = CURRENT_TIMESTAMP,
                error_message = NULL
            WHERE photo_id = ?
              AND collection_id = ?
              AND status = 'pending'
              AND EXISTS (
                  SELECT 1
                  FROM photos p
                  JOIN photo_face_authorizations a ON a.photo_id = p.id
                  WHERE p.id = photo_index_jobs.photo_id
                    AND {_AUTHORIZED_PHOTO_SQL}
              )
            """,
            (photo_id, SETTINGS.collection_id, SETTINGS.event_id)
        )
        return cursor.rowcount == 1


def _mark_job_failed(photo_id: int, message: str) -> None:
    with _connect() as connection:
        connection.execute(
            """
            UPDATE photo_index_jobs
            SET status = 'failed', error_message = ?
            WHERE photo_id = ? AND collection_id = ? AND status = 'processing'
            """,
            (message[:500], photo_id, SETTINGS.collection_id)
        )


def process_photo_index_job(photo_id: int) -> dict:
    """Index a claimed job's photo and store its faces.

    Failures mark the job 'failed' (with the error) instead of leaving it
    stuck in 'processing'. Retrying is safe: a re-index only adds extra
    face records that point at the same photo.
    """
    if not claim_photo_index_job(photo_id):
        return {"status": "not_claimed", "face_count": 0}

    # Faces already stored (e.g. indexed before this release): finish the
    # job from the database instead of indexing the same photo twice.
    with _connect() as connection:
        existing = connection.execute(
            "SELECT COUNT(*) FROM photo_faces WHERE photo_id = ? AND collection_id = ?",
            (photo_id, SETTINGS.collection_id)
        ).fetchone()[0]
        if existing:
            connection.execute(
                """
                UPDATE photo_index_jobs
                SET status = 'completed', face_count = ?,
                    completed_at = CURRENT_TIMESTAMP, error_message = NULL
                WHERE photo_id = ? AND collection_id = ? AND status = 'processing'
                """,
                (existing, photo_id, SETTINGS.collection_id)
            )
    if existing:
        logger.info("Photo %s already has %d indexed face(s); not re-indexing",
                    photo_id, existing)
        return {"status": "completed", "face_count": existing}

    try:
        face_ids = index_event_photo(photo_id)
        with _connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for face_id in face_ids:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO photo_faces
                        (photo_id, event_id, collection_id, rekognition_face_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (photo_id, SETTINGS.event_id, SETTINGS.collection_id, face_id)
                )
            connection.execute(
                """
                UPDATE photo_index_jobs
                SET status = 'completed', face_count = ?,
                    completed_at = CURRENT_TIMESTAMP, error_message = NULL
                WHERE photo_id = ? AND collection_id = ? AND status = 'processing'
                """,
                (len(face_ids), photo_id, SETTINGS.collection_id)
            )
    except (ClientError, BotoCoreError, FaceCollectionMissingError,
            PermissionError, sqlite3.Error) as error:
        logger.exception("Face indexing failed for photo %s", photo_id)
        _mark_job_failed(photo_id, f"{type(error).__name__}: {error}")
        raise

    logger.info("Indexed photo %s: %d face(s)", photo_id, len(face_ids))
    return {"status": "completed", "face_count": len(face_ids)}


def index_photo_now(photo_id: int) -> dict:
    """Queue and process one authorized photo. Used after upload/authorize."""
    register_photo_index_job(photo_id)
    return process_photo_index_job(photo_id)


def _prepare_selfie(selfie_bytes: bytes) -> bytes:
    if len(selfie_bytes) > MAX_SELFIE_BYTES:
        raise ValueError("Selfie exceeds the upload limit.")
    try:
        with Image.open(BytesIO(selfie_bytes)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1600, 1600))
            output = BytesIO()
            image.save(output, format="JPEG", quality=85)
            return output.getvalue()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Invalid selfie image.") from error


def find_matching_event_photos(selfie_bytes: bytes) -> list[dict]:
    """Return authorized, indexed photos matching the single face in a selfie.

    The selfie is neither stored nor indexed.
    """
    optimized = _prepare_selfie(selfie_bytes)
    client = get_tam_rekognition_client()

    detected = client.detect_faces(Image={"Bytes": optimized}, Attributes=["DEFAULT"])
    if len(detected.get("FaceDetails", [])) != 1:
        raise ValueError("Please take a selfie showing only one face.")

    result = client.search_faces_by_image(
        CollectionId=SETTINGS.collection_id,
        Image={"Bytes": optimized},
        FaceMatchThreshold=FACE_MATCH_THRESHOLD,
        MaxFaces=20
    )

    best: dict[int, float] = {}
    with _connect() as connection:
        for match in result.get("FaceMatches", []):
            row = connection.execute(
                f"""
                SELECT p.id
                FROM photo_faces f
                JOIN photos p ON p.id = f.photo_id
                JOIN photo_face_authorizations a ON a.photo_id = p.id
                JOIN photo_index_jobs j
                  ON j.photo_id = p.id AND j.collection_id = f.collection_id
                WHERE f.rekognition_face_id = ?
                  AND f.event_id = ?
                  AND f.collection_id = ?
                  AND j.status = 'completed'
                  AND {_AUTHORIZED_PHOTO_SQL}
                """,
                (match["Face"]["FaceId"], SETTINGS.event_id,
                 SETTINGS.collection_id, SETTINGS.event_id)
            ).fetchone()
            if row:
                similarity = round(match["Similarity"], 2)
                best[row[0]] = max(similarity, best.get(row[0], 0))

    return [{"photo_id": pid, "similarity": sim} for pid, sim in sorted(best.items())]
