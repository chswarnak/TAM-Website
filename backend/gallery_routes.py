"""
TAM Smart Event Photo Gallery

Purpose:
    Allow authorized event attendees and contributors to browse
    gallery-approved photographs without facial recognition.

Security:
    - AWS S3 remains private.
    - Event gallery must be enabled.
    - Each photograph must be approved for gallery sharing.
    - Guest viewing and downloading require valid authorization.
    - Contributors cannot edit or delete gallery photographs.
    - Downloads will be tracked.

"""

import sqlite3

from settings import SETTINGS

from fastapi import APIRouter

router = APIRouter()


def is_gallery_enabled(db_path: str, event_id: str) -> bool:
    """Check whether the Super Admin has enabled the event gallery."""

    with sqlite3.connect(db_path, timeout=10) as connection:
        event = connection.execute(
            """
            SELECT gallery_enabled
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

    return bool(event and event[0] == 1)


@router.get("/api/gallery/photos")
def list_gallery_photos(request: __import__("fastapi").Request, page: int = 1):
    """
    Return approved gallery photos to an authenticated contributor.
    No S3 credentials or private object keys are exposed.
    """
    from fastapi import HTTPException
    from database import DB_PATH

    event_id = SETTINGS.event_id

    # Accept active contributors or attendees with valid gallery sessions.
    viewer = get_gallery_viewer(
        request,
        DB_PATH,
        event_id,
    )

    if viewer is None:
        raise HTTPException(
            status_code=401,
            detail="Valid gallery session required"
        )

    if not is_gallery_enabled(DB_PATH, event_id):
        raise HTTPException(
            status_code=403,
            detail="Event gallery is disabled"
        )

    page = max(1, min(page, 10000))
    limit = 24
    offset = (page - 1) * limit

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row

        photos = connection.execute(
            """
            SELECT
                p.id,
                p.uploaded_at,
                c.name AS contributor_name
            FROM photos AS p
            JOIN contributors AS c
              ON c.id = p.contributor_id
            WHERE p.event_id = ?
              AND p.status = 'available'
              AND p.gallery_approved = 1
              AND p.s3_bucket = 'tam-smart-event-photos'
              AND p.s3_key IS NOT NULL
              AND p.s3_key != ''
            ORDER BY p.uploaded_at DESC, p.id DESC
            LIMIT ? OFFSET ?
            """,
            (event_id, limit, offset),
        ).fetchall()

    return {
        "event_id": event_id,
        "page": page,
        "photos": [dict(photo) for photo in photos]
    }


@router.get("/api/gallery/photos/{photo_id}/thumbnail")
def gallery_photo_thumbnail(
    photo_id: int,
    request: __import__("fastapi").Request,
):
    """
    Return a watermarked thumbnail to an authenticated contributor.

    This endpoint does not permit editing or deleting photographs.
    """
    from fastapi import HTTPException
    from fastapi.responses import Response
    from database import DB_PATH
    from aws_config import get_tam_s3_client
    from photo_watermark import create_guest_thumbnail

    event_id = SETTINGS.event_id

    # Allow approved contributors and authorized gallery attendees.
    viewer = get_gallery_viewer(
        request,
        DB_PATH,
        event_id,
    )

    if viewer is None:
        raise HTTPException(401, "Valid gallery session required")

    if not is_gallery_enabled(DB_PATH, event_id):
        raise HTTPException(403, "Event gallery is disabled")

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row

        photo = connection.execute(
            """
            SELECT
                p.s3_bucket,
                p.s3_key,
                c.name AS contributor_name
            FROM photos AS p
            JOIN contributors AS c
              ON c.id = p.contributor_id
            WHERE p.id = ?
              AND p.event_id = ?
              AND p.status = 'available'
              AND p.gallery_approved = 1
              AND p.s3_bucket = 'tam-smart-event-photos'
              AND p.s3_key IS NOT NULL
              AND p.s3_key != ''
            """,
            (photo_id, event_id),
        ).fetchone()

    if photo is None:
        raise HTTPException(404, "Photograph unavailable")

    image_bytes = get_tam_s3_client().get_object(
        Bucket=photo["s3_bucket"],
        Key=photo["s3_key"],
    )["Body"].read()

    thumbnail = create_guest_thumbnail(
        image_bytes,
        photo["contributor_name"],
    )

    # Revalidate viewer access after thumbnail processing.
    if (
        get_gallery_viewer(request, DB_PATH, event_id) != viewer
        or not is_gallery_enabled(DB_PATH, event_id)
    ):
        raise HTTPException(403, "Gallery access revoked")

    # Confirm that this photograph is still approved.
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        still_approved = connection.execute(
            """
            SELECT 1
            FROM photos
            WHERE id = ?
              AND event_id = ?
              AND status = 'available'
              AND gallery_approved = 1
            """,
            (photo_id, event_id),
        ).fetchone()

    if still_approved is None:
        raise HTTPException(
            403,
            "Photograph sharing permission revoked",
        )

    return Response(
        content=thumbnail,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/gallery/photos/{photo_id}/download")
def gallery_photo_download(
    photo_id: int,
    request: __import__("fastapi").Request,
):
    """
    Download a gallery-approved photograph and record the downloader.

    Only active, approved contributors can use this endpoint.
    """
    from datetime import datetime, timezone
    from fastapi import HTTPException
    from fastapi.responses import Response
    from database import DB_PATH
    from aws_config import get_tam_s3_client
    from photo_watermark import add_contributor_watermark

    event_id = SETTINGS.event_id

    # Accept contributors and attendees with valid gallery sessions.
    viewer = get_gallery_viewer(
        request,
        DB_PATH,
        event_id,
    )

    if viewer is None:
        raise HTTPException(401, "Valid gallery session required")

    if not is_gallery_enabled(DB_PATH, event_id):
        raise HTTPException(403, "Event gallery is disabled")

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row

        photo = connection.execute(
            """
            SELECT
                p.s3_bucket,
                p.s3_key,
                c.name AS contributor_name
            FROM photos AS p
            JOIN contributors AS c
              ON c.id = p.contributor_id
            WHERE p.id = ?
              AND p.event_id = ?
              AND p.status = 'available'
              AND p.gallery_approved = 1
              AND p.s3_bucket = 'tam-smart-event-photos'
              AND p.s3_key IS NOT NULL
              AND p.s3_key != ''
            """,
            (photo_id, event_id),
        ).fetchone()

    if photo is None:
        raise HTTPException(404, "Photograph unavailable")

    image_bytes = get_tam_s3_client().get_object(
        Bucket=photo["s3_bucket"],
        Key=photo["s3_key"],
    )["Body"].read()

    watermarked_bytes = add_contributor_watermark(
        image_bytes,
        photo["contributor_name"],
    )

    # Revalidate the viewer and gallery before recording.
    if (
        get_gallery_viewer(request, DB_PATH, event_id) != viewer
        or not is_gallery_enabled(DB_PATH, event_id)
    ):
        raise HTTPException(403, "Gallery access revoked")

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        with connection:
            approved = connection.execute(
                """
                SELECT 1
                FROM photos
                WHERE id = ?
                  AND event_id = ?
                  AND status = 'available'
                  AND gallery_approved = 1
                """,
                (photo_id, event_id),
            ).fetchone()

            if approved is None:
                raise HTTPException(403, "Photo sharing permission revoked")

            connection.execute(
                """
                INSERT INTO gallery_downloads (
                    event_id,
                    media_type,
                    media_id,
                    downloader_type,
                    downloader_id,
                    downloaded_at_utc
                )
                VALUES (?, 'photo', ?, ?, ?, ?)
                """,
                (
                    event_id,
                    photo_id,
                    viewer["type"],
                    viewer["id"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    return Response(
        content=watermarked_bytes,
        media_type="image/jpeg",
        headers={
            "Content-Disposition":
                f'attachment; filename="TAM_Photo_{photo_id}.jpg"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/gallery/guest/register")
def register_gallery_guest():
    """Removed: the anonymous access-code gallery registration.

    Gallery viewers now log in with an approved attendee or contributor
    account. 410 tells any cached old page that this flow is gone.
    """
    from fastapi import HTTPException

    raise HTTPException(
        status_code=410,
        detail="Please log in with your approved attendee account to view the gallery.",
    )


def get_gallery_viewer(request, db_path: str, event_id: str):
    """The logged-in, approved contributor or attendee viewing the gallery.

    Accounts with a pending password change get no access. db_path and
    event_id are kept for the existing callers; accounts reads the
    configured database and event.
    """
    import accounts

    for kind in ("contributor", "attendee"):
        account = accounts.session_account(request, kind)
        if account is not None and not account["must_change_password"]:
            return {"type": kind, "id": str(account["id"]),
                    "name": account["display_name"]}
    return None
