
"""TAM Smart Photos - shared photograph deletion authorization.

This module does not perform deletion.
AWS cleanup and audit execution will be connected separately.
"""

import sqlite3


class PhotoDeletionError(Exception):
    """A photograph deletion request cannot be authorized."""


def authorize_photo_deletion(
    db_path,
    photo_id,
    event_id,
    actor_type,
    actor_id,
):
    """Verify that the actor may delete the requested photograph."""

    if type(photo_id) is not int or photo_id <= 0:
        raise PhotoDeletionError("Invalid photograph ID")

    if type(actor_id) is not int or actor_id <= 0:
        raise PhotoDeletionError("Invalid user identity")

    if actor_type not in ("admin", "contributor"):
        raise PhotoDeletionError("Unsupported deletion role")

    with sqlite3.connect(
        db_path, timeout=15
    ) as connection:

        connection.row_factory = sqlite3.Row

        photo = connection.execute(
            """
            SELECT
                id,
                event_id,
                contributor_id,
                status,
                s3_bucket,
                s3_key
            FROM photos
            WHERE id = ?
              AND event_id = ?
            """,
            (photo_id, event_id),
        ).fetchone()

        if photo is None:
            raise PhotoDeletionError(
                "Photograph not found"
            )

        if photo["status"] != "available":
            raise PhotoDeletionError(
                "Photograph is not available for deletion"
            )

        if actor_type == "contributor":

            contributor = connection.execute(
                """
                SELECT id
                FROM contributors
                WHERE id = ?
                  AND event_id = ?
                  AND approved = 1
                  AND active = 1
                  AND deleted_at IS NULL
                """,
                (actor_id, event_id),
            ).fetchone()

            if contributor is None:
                raise PhotoDeletionError(
                    "Contributor account is not authorized"
                )

            if photo["contributor_id"] != actor_id:
                raise PhotoDeletionError(
                    "You can delete only your own photographs"
                )

        else:

            admin = connection.execute(
                """
                SELECT id
                FROM admins
                WHERE id = ?
                  AND role IN ('admin', 'super_admin')
                  AND active = 1
                  AND deleted_at IS NULL
                  AND must_change_password = 0
                """,
                (actor_id,),
            ).fetchone()

            if admin is None:
                raise PhotoDeletionError(
                    "Administrator is not authorized"
                )

        if (
            photo["s3_bucket"] != "tam-smart-event-photos"
            or not photo["s3_key"]
        ):
            raise PhotoDeletionError(
                "Photograph storage information is invalid"
            )

        return {
            "photo_id": photo["id"],
            "event_id": photo["event_id"],
            "original_contributor_id": photo["contributor_id"],
            "actor_type": actor_type,
            "actor_id": actor_id,
            "s3_bucket": photo["s3_bucket"],
            "s3_key": photo["s3_key"],
        }


# TAM-SHARED-AWS-PHOTO-CLEANUP-V1

ALLOWED_PHOTO_BUCKET = "tam-smart-event-photos"
ALLOWED_EVENT_PREFIX = "tam-diwali-2026/"
ALLOWED_FACE_COLLECTION = "tam-diwali-2026-faces"


def delete_photo_from_s3(s3_client, bucket, key):
    """
    Delete one event photograph from S3.

    The caller must first verify the authenticated user's
    deletion permissions and record the deletion request.

    This function never deletes an entire folder or bucket.
    """

    if bucket != ALLOWED_PHOTO_BUCKET:
        raise PhotoDeletionError(
            "Unauthorized S3 bucket"
        )

    if not isinstance(key, str):
        raise PhotoDeletionError(
            "Invalid S3 object key"
        )

    if (
        not key.startswith(ALLOWED_EVENT_PREFIX)
        or key.endswith("/")
        or ".." in key.split("/")
    ):
        raise PhotoDeletionError(
            "Unauthorized S3 object path"
        )

    response = s3_client.delete_object(
        Bucket=bucket,
        Key=key
    )

    status = response.get(
        "ResponseMetadata", {}
    ).get("HTTPStatusCode")

    if status != 204:
        raise PhotoDeletionError(
            "S3 did not confirm the deletion request"
        )

    return {
        "status": "delete_request_accepted",
        "bucket": bucket,
        "key": key
    }


def delete_photo_faces_from_rekognition(
    rekognition_client,
    collection_id,
    face_ids,
):
    """
    Remove indexed faces belonging to one photograph.

    Face IDs must come from the authenticated photograph's
    database records, never from browser-supplied values.

    Raises an error if AWS reports unsuccessful deletions.
    """

    if collection_id != ALLOWED_FACE_COLLECTION:
        raise PhotoDeletionError(
            "Unauthorized Rekognition collection"
        )

    if not isinstance(face_ids, (list, tuple)):
        raise PhotoDeletionError(
            "Invalid Rekognition Face ID list"
        )

    if not face_ids:
        return {
            "status": "no_faces",
            "deleted_count": 0
        }

    if (
        len(face_ids) > 4096
        or any(
            not isinstance(face_id, str)
            or not face_id.strip()
            for face_id in face_ids
        )
    ):
        raise PhotoDeletionError(
            "Invalid Rekognition Face IDs"
        )

    response = rekognition_client.delete_faces(
        CollectionId=collection_id,
        FaceIds=face_ids
    )

    unsuccessful = response.get(
        "UnsuccessfulFaceDeletions", []
    )

    if unsuccessful:
        raise PhotoDeletionError(
            "Rekognition reported incomplete face deletion"
        )

    deleted_ids = response.get(
        "DeletedFaces", []
    )

    if set(deleted_ids) != set(face_ids):
        raise PhotoDeletionError(
            "Rekognition did not confirm all face deletions"
        )

    return {
        "status": "completed",
        "deleted_count": len(deleted_ids)
    }


# TAM-PHOTO-DELETION-REQUEST-V1

def begin_photo_deletion(
    db_path,
    photo_id,
    event_id,
    actor_type,
    actor_id,
    deletion_source,
):
    """
    Reserve one photograph for deletion.

    No AWS calls are made here.
    The actual deletion worker will process the reservation.
    """

    allowed_sources = {
        "admin": "Admin Gallery Management",
        "contributor": "View Event Photos",
    }

    if allowed_sources.get(actor_type) != deletion_source:
        raise PhotoDeletionError("Invalid deletion source")

    if type(photo_id) is not int or photo_id <= 0:
        raise PhotoDeletionError("Invalid photograph ID")

    if type(actor_id) is not int or actor_id <= 0:
        raise PhotoDeletionError("Invalid user identity")

    with sqlite3.connect(db_path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")

        try:
            photo = connection.execute(
                """
                SELECT id, contributor_id, status,
                       s3_bucket, s3_key
                FROM photos
                WHERE id = ? AND event_id = ?
                """,
                (photo_id, event_id),
            ).fetchone()

            if photo is None or photo["status"] != "available":
                raise PhotoDeletionError(
                    "Photograph is not available for deletion"
                )

            if (
                photo["s3_bucket"] != ALLOWED_PHOTO_BUCKET
                or not isinstance(photo["s3_key"], str)
                or not photo["s3_key"].startswith(ALLOWED_EVENT_PREFIX)
                or photo["s3_key"].endswith("/")
                or ".." in photo["s3_key"].split("/")
            ):
                raise PhotoDeletionError(
                    "Invalid photograph storage location"
                )

            if actor_type == "contributor":
                account = connection.execute(
                    """
                    SELECT id FROM contributors
                    WHERE id = ? AND event_id = ?
                      AND approved = 1
                      AND active = 1
                      AND deleted_at IS NULL
                    """,
                    (actor_id, event_id),
                ).fetchone()

                if (
                    account is None
                    or photo["contributor_id"] != actor_id
                ):
                    raise PhotoDeletionError(
                        "Contributor cannot delete this photograph"
                    )

            elif actor_type == "admin":
                account = connection.execute(
                    """
                    SELECT id FROM admins
                    WHERE id = ?
                      AND role IN ('admin', 'super_admin')
                      AND active = 1
                      AND deleted_at IS NULL
                      AND must_change_password = 0
                    """,
                    (actor_id,),
                ).fetchone()

                if account is None:
                    raise PhotoDeletionError(
                        "Administrator is not authorized"
                    )

            else:
                raise PhotoDeletionError(
                    "Unsupported deletion role"
                )

            existing = connection.execute(
                """
                SELECT id FROM photo_deletion_operations
                WHERE photo_id = ?
                """,
                (photo_id,),
            ).fetchone()

            if existing is not None:
                raise PhotoDeletionError(
                    "Photograph already has a deletion request"
                )

            connection.execute(
                """
                INSERT INTO photo_deletion_operations (
                    photo_id,
                    event_id,
                    original_contributor_id,
                    actor_type,
                    actor_id,
                    deletion_source,
                    cleanup_status,
                    s3_cleanup_status,
                    rekognition_cleanup_status
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending',
                        'pending', 'pending')
                """,
                (
                    photo_id,
                    event_id,
                    photo["contributor_id"],
                    actor_type,
                    actor_id,
                    deletion_source,
                ),
            )

            cursor = connection.execute(
                """
                UPDATE photos
                SET status = 'deletion_pending',
                    gallery_approved = 0
                WHERE id = ?
                  AND event_id = ?
                  AND status = 'available'
                """,
                (photo_id, event_id),
            )

            if cursor.rowcount != 1:
                raise PhotoDeletionError(
                    "Unable to reserve photograph for deletion"
                )

            connection.commit()

            return {
                "photo_id": photo_id,
                "status": "pending",
                "message": "Deletion request recorded",
            }

        except Exception:
            connection.rollback()
            raise


# TAM-PHOTO-DELETION-PROCESSOR-V1

def process_photo_deletion(
    db_path,
    photo_id,
    s3_client,
    rekognition_client,
):
    """
    Process a previously authorized deletion request.

    Must only be called after begin_photo_deletion()
    has committed the deletion reservation.

    This function is not a public HTTP endpoint.
    """

    if type(photo_id) is not int or photo_id <= 0:
        raise PhotoDeletionError("Invalid photograph ID")

    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")

        job = conn.execute(
            """
            SELECT
                op.id AS operation_id,
                op.event_id,
                op.cleanup_status,
                op.s3_cleanup_status,
                op.rekognition_cleanup_status,
                p.status AS photo_status,
                p.s3_bucket,
                p.s3_key
            FROM photo_deletion_operations op
            JOIN photos p ON p.id = op.photo_id
            WHERE op.photo_id = ?
            """,
            (photo_id,),
        ).fetchone()

        if job is None:
            raise PhotoDeletionError(
                "No authorized deletion request exists"
            )

        if (
            job["event_id"] != "tam-diwali-2026"
            or job["photo_status"] != "deletion_pending"
            or job["cleanup_status"] not in ("pending", "failed")
        ):
            raise PhotoDeletionError(
                "Deletion request cannot be processed"
            )

        if (
            job["s3_bucket"] != ALLOWED_PHOTO_BUCKET
            or not isinstance(job["s3_key"], str)
            or not job["s3_key"].startswith(
                ALLOWED_EVENT_PREFIX
            )
            or job["s3_key"].endswith("/")
            or ".." in job["s3_key"].split("/")
        ):
            raise PhotoDeletionError(
                "Invalid photograph storage location"
            )

        faces = conn.execute(
            """
            SELECT collection_id, rekognition_face_id
            FROM photo_faces
            WHERE photo_id = ?
            """,
            (photo_id,),
        ).fetchall()

        if any(
            row["collection_id"] != ALLOWED_FACE_COLLECTION
            or not row["rekognition_face_id"]
            for row in faces
        ):
            raise PhotoDeletionError(
                "Unexpected face-index collection"
            )

        face_ids = list(dict.fromkeys(
            row["rekognition_face_id"] for row in faces
        ))

        operation_id = job["operation_id"]
        bucket = job["s3_bucket"]
        key = job["s3_key"]
        s3_done = job["s3_cleanup_status"] == "completed"
        rekognition_done = (
            job["rekognition_cleanup_status"] == "completed"
        )

        cursor = conn.execute(
            """
            UPDATE photo_deletion_operations
            SET cleanup_status = 'processing',
                error_message = NULL
            WHERE id = ?
              AND cleanup_status IN ('pending', 'failed')
            """,
            (operation_id,),
        )

        if cursor.rowcount != 1:
            raise PhotoDeletionError(
                "Deletion operation is already processing"
            )

        conn.commit()

    def update_operation(**fields):
        allowed = {
            "cleanup_status",
            "s3_cleanup_status",
            "rekognition_cleanup_status",
            "error_message",
        }

        if not fields or not set(fields).issubset(allowed):
            raise PhotoDeletionError(
                "Invalid audit update"
            )

        assignments = ", ".join(
            key + " = ?" for key in fields
        )

        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute(
                "UPDATE photo_deletion_operations SET "
                + assignments +
                " WHERE id = ?",
                tuple(fields.values()) + (operation_id,),
            )
            conn.commit()

    try:
        # TAM-VERIFIED-DELETION-PROCESSOR-V1

        # S3 DeleteObject is idempotent for this workflow.
        # Verify absence even after a previous successful step.

        if not s3_done:
            delete_photo_from_s3(
                s3_client,
                bucket,
                key,
            )

        verify_photo_absent_from_s3(
            s3_client,
            bucket,
            key,
        )

        if not s3_done:
            update_operation(
                s3_cleanup_status="completed"
            )

        # TAM-REKOGNITION-PAGINATION-FIX-V1
        # Search all pages before deciding which faces remain.

        target_faces = set(face_ids)
        remaining_faces = set()

        next_token = None
        seen_tokens = set()

        while True:
            kwargs = {
                "CollectionId": ALLOWED_FACE_COLLECTION,
                "MaxResults": 1000,
            }

            if next_token:
                kwargs["NextToken"] = next_token

            response = rekognition_client.list_faces(**kwargs)

            existing = {
                face.get("FaceId")
                for face in response.get("Faces", [])
            }

            remaining_faces.update(
                target_faces.intersection(existing)
            )

            next_token = response.get("NextToken")

            if not next_token:
                break

            if next_token in seen_tokens:
                raise PhotoDeletionError(
                    "Rekognition pagination loop detected"
                )

            seen_tokens.add(next_token)

        if remaining_faces:
            delete_photo_faces_from_rekognition(
                rekognition_client,
                ALLOWED_FACE_COLLECTION,
                sorted(remaining_faces),
            )

        verify_faces_absent_from_rekognition(
            rekognition_client,
            ALLOWED_FACE_COLLECTION,
            face_ids,
        )

        if not rekognition_done:
            update_operation(
                rekognition_cleanup_status="completed"
            )

        with sqlite3.connect(db_path, timeout=30) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")

            # TAM-DELETION-DATABASE-CLEANUP-V1
            # Remove obsolete search and indexing references.
            # Preserve photo metadata and download audit history.

            conn.execute(
                """
                DELETE FROM guest_session_photos
                WHERE photo_id = ?
                """,
                (photo_id,),
            )

            conn.execute(
                """
                DELETE FROM photo_faces
                WHERE photo_id = ?
                """,
                (photo_id,),
            )

            conn.execute(
                """
                DELETE FROM photo_index_jobs
                WHERE photo_id = ?
                """,
                (photo_id,),
            )

            cursor = conn.execute(
                """
                UPDATE photos
                SET status = 'deleted',
                    gallery_approved = 0
                WHERE id = ?
                  AND status = 'deletion_pending'
                """,
                (photo_id,),
            )

            if cursor.rowcount != 1:
                raise PhotoDeletionError(
                    "Photograph status changed unexpectedly"
                )

            # TAM-DELETION-FINAL-TRANSACTION-V1
            audit_cursor = conn.execute(
                """
                UPDATE photo_deletion_operations
                SET cleanup_status = 'completed',
                    completed_at_utc = CURRENT_TIMESTAMP,
                    error_message = NULL
                WHERE id = ?
                  AND cleanup_status = 'processing'
                  AND s3_cleanup_status = 'completed'
                  AND rekognition_cleanup_status = 'completed'
                """,
                (operation_id,),
            )

            if audit_cursor.rowcount != 1:
                raise PhotoDeletionError(
                    "Deletion audit could not be completed"
                )

            conn.commit()

        return {
            "photo_id": photo_id,
            "status": "completed",
        }

    except Exception as error:
        try:
            update_operation(
                cleanup_status="failed",
                error_message=type(error).__name__,
            )
        except Exception:
            pass

        raise PhotoDeletionError(
            "Photo deletion cleanup was not completed; "
            "review the audit before retrying"
        ) from error


# TAM-DELETION-RECOVERY-V1

def recover_interrupted_photo_deletion(
    db_path,
    photo_id,
    confirmation,
):
    """
    Manually recover an interrupted deletion.

    IMPORTANT:
    The operator must first establish that the original
    deletion worker is no longer running.

    This function performs no AWS deletion.
    It only makes a stuck operation eligible for retry.
    """

    if confirmation != "RECOVER_INTERRUPTED_DELETION":
        raise PhotoDeletionError(
            "Explicit recovery confirmation required"
        )

    if type(photo_id) is not int or photo_id <= 0:
        raise PhotoDeletionError(
            "Invalid photograph ID"
        )

    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row

        conn.execute("BEGIN IMMEDIATE")

        operation = conn.execute(
            """
            SELECT op.id,
                   op.cleanup_status,
                   p.status AS photo_status
            FROM photo_deletion_operations op
            JOIN photos p ON p.id = op.photo_id
            WHERE op.photo_id = ?
            """,
            (photo_id,),
        ).fetchone()

        if operation is None:
            raise PhotoDeletionError(
                "Deletion operation not found"
            )

        if (
            operation["cleanup_status"] != "processing"
            or operation["photo_status"] != "deletion_pending"
        ):
            raise PhotoDeletionError(
                "Operation is not eligible for recovery"
            )

        cursor = conn.execute(
            """
            UPDATE photo_deletion_operations
            SET cleanup_status = 'failed',
                error_message =
                    'Manually recovered interrupted operation'
            WHERE id = ?
              AND cleanup_status = 'processing'
            """,
            (operation["id"],),
        )

        if cursor.rowcount != 1:
            raise PhotoDeletionError(
                "Recovery status update failed"
            )

        conn.commit()

    return {
        "photo_id": photo_id,
        "status": "ready_for_retry",
        "message": "Interrupted operation marked for retry",
    }


# TAM-DELETION-VERIFICATION-V1

def verify_photo_absent_from_s3(s3_client, bucket, key):
    """
    Confirm that an S3 photograph no longer exists.

    An authorization failure must never be treated as proof
    that an object has been deleted.
    """
    from botocore.exceptions import ClientError

    if bucket != ALLOWED_PHOTO_BUCKET:
        raise PhotoDeletionError("Unauthorized S3 bucket")

    if (
        not isinstance(key, str)
        or not key.startswith(ALLOWED_EVENT_PREFIX)
        or key.endswith("/")
        or ".." in key.split("/")
    ):
        raise PhotoDeletionError("Unauthorized S3 object path")

    try:
        s3_client.head_object(
            Bucket=bucket,
            Key=key,
        )

    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")

        if code in ("404", "NoSuchKey", "NotFound"):
            return True

        raise PhotoDeletionError(
            "Unable to verify S3 object removal"
        ) from error

    raise PhotoDeletionError(
        "S3 photograph still exists after deletion"
    )


def verify_faces_absent_from_rekognition(
    rekognition_client,
    collection_id,
    face_ids,
):
    """
    Confirm that none of the specified Face IDs remain.

    Checks every page of the Rekognition collection.
    An API failure is never treated as successful cleanup.
    """

    if collection_id != ALLOWED_FACE_COLLECTION:
        raise PhotoDeletionError(
            "Unauthorized Rekognition collection"
        )

    if not isinstance(face_ids, (list, tuple)):
        raise PhotoDeletionError("Invalid Face ID list")

    if any(
        not isinstance(face_id, str) or not face_id.strip()
        for face_id in face_ids
    ):
        raise PhotoDeletionError("Invalid Face ID")

    if not face_ids:
        return True

    targets = set(face_ids)
    token = None
    seen_tokens = set()

    while True:
        kwargs = {
            "CollectionId": collection_id,
            "MaxResults": 1000,
        }

        if token:
            kwargs["NextToken"] = token

        response = rekognition_client.list_faces(**kwargs)

        existing_ids = {
            face.get("FaceId")
            for face in response.get("Faces", [])
        }

        remaining = targets.intersection(existing_ids)

        if remaining:
            raise PhotoDeletionError(
                "Rekognition still contains indexed faces "
                "belonging to this photograph"
            )

        token = response.get("NextToken")

        if not token:
            return True

        if token in seen_tokens:
            raise PhotoDeletionError(
                "Unexpected Rekognition pagination loop"
            )

        seen_tokens.add(token)
