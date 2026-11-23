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

The gallery routes are not registered with FastAPI yet.
"""

import sqlite3

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


def get_active_contributor(db_path: str, event_id: str, contributor_id):
    """
    Return an approved, active contributor.

    A disabled or soft-deleted contributor must not receive
    gallery access, even if they have an existing browser session.
    """
    if contributor_id is None:
        return None

    with sqlite3.connect(db_path, timeout=10) as connection:
        connection.row_factory = sqlite3.Row

        contributor = connection.execute(
            """
            SELECT id, name
            FROM contributors
            WHERE id = ?
              AND event_id = ?
              AND approved = 1
              AND active = 1
              AND deleted_at IS NULL
            """,
            (contributor_id, event_id),
        ).fetchone()

    return dict(contributor) if contributor else None


@router.get("/api/gallery/photos")
def list_gallery_photos(request: __import__("fastapi").Request, page: int = 1):
    """
    Return approved gallery photos to an authenticated contributor.
    No S3 credentials or private object keys are exposed.
    """
    from fastapi import HTTPException
    from database import DB_PATH

    event_id = "tam-diwali-2026"

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

    event_id = "tam-diwali-2026"

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

    event_id = "tam-diwali-2026"

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
def register_gallery_guest(
    request: __import__("fastapi").Request,
    attendee_name: str = __import__("fastapi").Form(...),
    attendee_mobile: str = __import__("fastapi").Form(...),
    gallery_access_code: str = __import__("fastapi").Form(...),
    gallery_acknowledged: bool = __import__("fastapi").Form(False),
):
    """
    Register an attendee for temporary gallery browsing.

    This is separate from facial-recognition consent.
    Name and mobile number are self-reported, not verified.
    """
    import re

    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    from database import DB_PATH
    from gallery_sessions import (
        create_gallery_session,
        GALLERY_SESSION_DURATION,
    )

    event_id = "tam-diwali-2026"

    if not is_gallery_enabled(DB_PATH, event_id):
        raise HTTPException(403, "Event gallery is disabled")

    # Trust visitor IP headers only from our local Nginx proxy.
    import ipaddress
    import os
    from gallery_rate_limit import check_gallery_registration_limit

    # Nginx overwrites X-Real-IP with the connecting visitor address.
    # Uvicorn is bound to localhost and cannot be reached directly
    # through the public EC2 network interface.
    forwarded_ip = request.headers.get("X-Real-IP", "")

    try:
        visitor_ip = str(ipaddress.ip_address(forwarded_ip))
    except ValueError:
        raise HTTPException(403, "Invalid visitor identification")

    allowed = check_gallery_registration_limit(
        DB_PATH,
        visitor_ip,
        os.environ["SESSION_SECRET"],
    )

    if not allowed:
        raise HTTPException(
            429,
            "Too many registration attempts. Try again later.",
        )

    name = attendee_name.strip()
    mobile = attendee_mobile.strip()

    if not gallery_acknowledged:
        raise HTTPException(
            400,
            "You must acknowledge the event gallery sharing notice",
        )

    if not 2 <= len(name) <= 100:
        raise HTTPException(400, "Invalid attendee name")

    if not 7 <= len(mobile) <= 20:
        raise HTTPException(400, "Invalid mobile number")

    if not re.fullmatch(r"[+0-9() .-]+", mobile):
        raise HTTPException(400, "Invalid mobile number format")

    # Verify the event access code before creating a session.
    from gallery_access_code import verify_gallery_access_code

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        event = connection.execute(
            """
            SELECT gallery_access_code_hash
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

    if event is None or not event[0]:
        raise HTTPException(403, "Gallery access code not configured")

    if not verify_gallery_access_code(
        gallery_access_code,
        event[0],
    ):
        raise HTTPException(403, "Invalid gallery access code")

    # Do not overwrite an existing session unless registration succeeds.
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        token = create_gallery_session(
            connection,
            event_id,
            name,
            mobile,
        )

    response = JSONResponse({
        "status": "registered",
        "message": "Gallery access granted for 30 minutes.",
        "identity_verified": False,
    })

    response.set_cookie(
        key="tam_gallery_session",
        value=token,
        max_age=GALLERY_SESSION_DURATION,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/gallery",
    )

    return response


def get_gallery_viewer(request, db_path: str, event_id: str):
    """
    Identify an authorized gallery viewer.

    Supports active contributors and registered attendees.
    Does not perform facial recognition.
    """
    from gallery_sessions import validate_gallery_session

    # Existing contributor authentication.
    contributor_id = request.session.get("contributor_id")

    contributor = get_active_contributor(
        db_path,
        event_id,
        contributor_id,
    )

    if contributor is not None:
        return {
            "type": "contributor",
            "id": str(contributor["id"]),
            "name": contributor["name"],
        }

    # An existing but invalid contributor session must not
    # fall back to attendee authorization.
    if contributor_id is not None:
        return None

    # Separate temporary attendee gallery session.
    token = request.cookies.get("tam_gallery_session")

    if not token:
        return None

    with sqlite3.connect(db_path, timeout=10) as connection:
        attendee = validate_gallery_session(
            connection,
            token,
            event_id,
        )

    if attendee is None:
        return None

    return {
        "type": "attendee",
        "id": attendee["token_hash"],
        "name": attendee["attendee_name"],
    }
