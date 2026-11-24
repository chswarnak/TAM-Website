import sqlite3
from database import DB_PATH
from aws_config import get_tam_aws_session

TAM_REKOGNITION_REGION = "ca-central-1"
TAM_COLLECTION_ID = "tam-diwali-2026-faces"


def get_tam_rekognition_client():
    """
    Create an AWS Rekognition client using
    TAM's configured AWS credentials.
    """
    session = get_tam_aws_session()

    return session.client(
        "rekognition",
        region_name=TAM_REKOGNITION_REGION
    )


def get_collection_status():
    """
    Return the TAM collection status.
    Does not index or modify any faces.
    """
    client = get_tam_rekognition_client()

    result = client.describe_collection(
        CollectionId=TAM_COLLECTION_ID
    )

    return {
        "collection_id": TAM_COLLECTION_ID,
        "face_count": result["FaceCount"],
        "model_version": result["FaceModelVersion"]
    }



def index_event_photo(photo_id: int):
    """
    Index an authorized TAM event photograph.

    Deny indexing unless the photograph has
    an explicit authorization record.
    """
    with sqlite3.connect(DB_PATH) as connection:
        photo = connection.execute(
            """
            SELECT
                p.event_id,
                p.s3_bucket,
                p.s3_key
            FROM photos p
            JOIN photo_face_authorizations a
                ON a.photo_id = p.id
            WHERE p.id = ?
              AND p.status = 'available'
              AND a.authorized = 1
              AND a.consent_reference IS NOT NULL
              AND TRIM(a.consent_reference) != ''
            """,
            (photo_id,)
        ).fetchone()

    if photo is None:
        raise PermissionError(
            "Photo is not authorized for facial indexing."
        )

    event_id, bucket, s3_key = photo

    if event_id != "tam-diwali-2026":
        raise PermissionError("Wrong TAM event.")

    if bucket != "tam-smart-event-photos" or not s3_key:
        raise ValueError("Invalid TAM S3 reference.")

    # Prevent indexing a photograph that already has
    # registered face records in this collection.
    with sqlite3.connect(DB_PATH) as connection:
        existing = connection.execute(
            """
            SELECT COUNT(*)
            FROM photo_faces
            WHERE photo_id = ?
              AND collection_id = ?
            """,
            (photo_id, TAM_COLLECTION_ID)
        ).fetchone()[0]

    if existing > 0:
        raise ValueError(
            "This photograph already has indexed faces."
        )

    client = get_tam_rekognition_client()

    
    result = client.index_faces(
        CollectionId=TAM_COLLECTION_ID,
        Image={
            "S3Object": {
                "Bucket": bucket,
                "Name": s3_key
            }
        },
        ExternalImageId=f"tam_photo_{photo_id}",
        DetectionAttributes=[],
        MaxFaces=100,
        QualityFilter="AUTO"
    )


    return [
        record["Face"]["FaceId"]
        for record in result.get("FaceRecords", [])
    ]



def save_indexed_faces(photo_id: int, face_ids: list[str]):
    """
    Link previously indexed Rekognition faces
    to an existing TAM photograph in SQLite.
    """

    if not face_ids:
        return 0

    if len(face_ids) != len(set(face_ids)):
        raise ValueError("Duplicate face IDs provided.")

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA foreign_keys = ON")

        photo = connection.execute(
            """
            SELECT event_id, s3_bucket, s3_key
            FROM photos
            WHERE id = ?
              AND status = 'available'
            """,
            (photo_id,)
        ).fetchone()

        if photo is None:
            raise ValueError("Photo not found or unavailable.")

        event_id, bucket, s3_key = photo

        if event_id != "tam-diwali-2026":
            raise ValueError("Wrong event.")

        if bucket != "tam-smart-event-photos" or not s3_key:
            raise ValueError("Photo has no valid TAM S3 reference.")

        for face_id in face_ids:
            if not face_id:
                raise ValueError("Empty face ID.")

            connection.execute(
                """
                INSERT OR IGNORE INTO photo_faces (
                    photo_id,
                    event_id,
                    collection_id,
                    rekognition_face_id
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    photo_id,
                    event_id,
                    TAM_COLLECTION_ID,
                    face_id
                )
            )

        connection.commit()

        return len(face_ids)


def register_photo_index_job(photo_id: int):
    """
    Register one photo for face indexing.

    Requires an authorized photo and consent reference.
    Does not contact AWS or index any faces.
    """
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")

        photo = connection.execute(
            """
            SELECT p.id
            FROM photos p
            JOIN photo_face_authorizations a
                ON a.photo_id = p.id
            WHERE p.id = ?
              AND p.event_id = 'tam-diwali-2026'
              AND p.status = 'available'
              AND p.s3_bucket = 'tam-smart-event-photos'
              AND p.s3_key IS NOT NULL
              AND a.authorized = 1
              AND a.consent_reference IS NOT NULL
              AND TRIM(a.consent_reference) != ''
            """,
            (photo_id,)
        ).fetchone()

        if photo is None:
            raise PermissionError(
                "Photo is not authorized for facial indexing."
            )

        connection.execute(
            """
            INSERT OR IGNORE INTO photo_index_jobs (
                photo_id,
                collection_id,
                status
            )
            VALUES (?, ?, 'pending')
            """,
            (photo_id, TAM_COLLECTION_ID)
        )

        job = connection.execute(
            """
            SELECT id, status
            FROM photo_index_jobs
            WHERE photo_id = ?
              AND collection_id = ?
            """,
            (photo_id, TAM_COLLECTION_ID)
        ).fetchone()

        connection.commit()

    return {
        "job_id": job[0],
        "status": job[1]
    }


def claim_photo_index_job(photo_id: int):
    """
    Atomically claim one authorized, pending indexing job.

    Returns True if this worker claimed the job.
    Returns False if the job is not pending or does not exist.
    Does not contact AWS.
    """
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute("BEGIN IMMEDIATE")

        cursor = connection.execute(
            """
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
                  JOIN photo_face_authorizations a
                    ON a.photo_id = p.id
                  WHERE p.id = photo_index_jobs.photo_id
                    AND p.event_id = 'tam-diwali-2026'
                    AND p.status = 'available'
                    AND p.s3_bucket = 'tam-smart-event-photos'
                    AND p.s3_key IS NOT NULL
                    AND a.authorized = 1
                    AND a.consent_reference IS NOT NULL
                    AND TRIM(a.consent_reference) != ''
              )
            """,
            (photo_id, TAM_COLLECTION_ID)
        )

        claimed = cursor.rowcount == 1
        connection.commit()

    return claimed


def process_photo_index_job(photo_id: int):
    """
    Process one previously registered and authorized job.

    An interrupted or failed job is not automatically retried.
    This prevents accidental duplicate indexing in AWS.
    """

    if not claim_photo_index_job(photo_id):
        return {
            "status": "not_claimed",
            "face_count": 0
        }

    # The job is now marked 'processing'.
    # Do not retry it automatically if an error occurs.
    try:
        face_ids = index_event_photo(photo_id)

        with sqlite3.connect(DB_PATH, timeout=10) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")

            job = connection.execute(
                """
                SELECT status
                FROM photo_index_jobs
                WHERE photo_id = ?
                  AND collection_id = ?
                """,
                (photo_id, TAM_COLLECTION_ID)
            ).fetchone()

            if job is None or job[0] != "processing":
                raise RuntimeError("Indexing job state changed.")

            # Store every detected face.
            for face_id in face_ids:
                connection.execute(
                    """
                    INSERT INTO photo_faces (
                        photo_id,
                        event_id,
                        collection_id,
                        rekognition_face_id
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        photo_id,
                        "tam-diwali-2026",
                        TAM_COLLECTION_ID,
                        face_id
                    )
                )

            # Complete the job even if zero faces were found.
            connection.execute(
                """
                UPDATE photo_index_jobs
                SET status = 'completed',
                    face_count = ?,
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = NULL
                WHERE photo_id = ?
                  AND collection_id = ?
                  AND status = 'processing'
                """,
                (
                    len(face_ids),
                    photo_id,
                    TAM_COLLECTION_ID
                )
            )

            connection.commit()

        return {
            "status": "completed",
            "face_count": len(face_ids)
        }

    except Exception:
        # AWS might already have indexed faces.
        # Leave the job in 'processing' for manual
        # investigation instead of automatically retrying.
        with sqlite3.connect(DB_PATH) as connection:
            connection.execute(
                """
                UPDATE photo_index_jobs
                SET error_message = ?
                WHERE photo_id = ?
                  AND collection_id = ?
                  AND status = 'processing'
                """,
                (
                    "Indexing interrupted; reconciliation required",
                    photo_id,
                    TAM_COLLECTION_ID
                )
            )
            connection.commit()

        raise


def find_matching_event_photos(selfie_bytes: bytes):
    """
    Search for authorized TAM event photos using one selfie.
    Returns matched photo IDs and similarity scores.
    Does not save or index the selfie.
    """
    from io import BytesIO
    from PIL import Image, ImageOps

    if len(selfie_bytes) > 20 * 1024 * 1024:
        raise ValueError("Selfie exceeds the upload limit.")

    try:
        with Image.open(BytesIO(selfie_bytes)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((1600, 1600))

            output = BytesIO()
            image.save(output, format="JPEG", quality=85)
            optimized = output.getvalue()
    except Exception as exc:
        raise ValueError("Invalid selfie image.") from exc

    client = get_tam_rekognition_client()

    detected = client.detect_faces(
        Image={"Bytes": optimized},
        Attributes=["DEFAULT"]
    )

    if len(detected.get("FaceDetails", [])) != 1:
        raise ValueError("Please take a selfie showing only one face.")

    result = client.search_faces_by_image(
        CollectionId=TAM_COLLECTION_ID,
        Image={"Bytes": optimized},
        FaceMatchThreshold=95,
        MaxFaces=4096
    )

    matches = []

    with sqlite3.connect(DB_PATH) as connection:
        for match in result.get("FaceMatches", []):
            face_id = match["Face"]["FaceId"]

            row = connection.execute(
                """
                SELECT p.id
                FROM photo_faces f
                JOIN photos p
                  ON p.id = f.photo_id
                JOIN photo_face_authorizations a
                  ON a.photo_id = p.id
                JOIN photo_index_jobs j
                  ON j.photo_id = p.id
                 AND j.collection_id = f.collection_id
                WHERE f.rekognition_face_id = ?
                  AND f.event_id = ?
                  AND f.collection_id = ?
                  AND p.event_id = ?
                  AND p.status = 'available'
                  AND p.s3_bucket = 'tam-smart-event-photos'
                  AND p.s3_key IS NOT NULL
                  AND a.authorized = 1
                  AND a.consent_reference IS NOT NULL
                  AND TRIM(a.consent_reference) != ''
                  AND j.status = 'completed'
                """,
                (
                    face_id,
                    "tam-diwali-2026",
                    TAM_COLLECTION_ID,
                    "tam-diwali-2026"
                )
            ).fetchone()

            if row:
                matches.append({
                    "photo_id": row[0],
                    "similarity": round(match["Similarity"], 2)
                })

    return matches
