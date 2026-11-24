
"""
TAM Smart Event Photo Sharing System.

Super Admin authorization for gallery management.

This module does not enable the gallery or modify photographs.
"""

import sqlite3

from settings import SETTINGS

from fastapi import APIRouter, HTTPException, Request

from database import DB_PATH


router = APIRouter()


def require_gallery_super_admin(
    request: Request,
    db_path: str = DB_PATH,
) -> int:
    """
    Return the authenticated Super Admin ID.

    Reject missing, disabled, deleted, or incomplete accounts.
    Never trust an administrator role supplied by the browser.
    """

    admin_id = request.session.get("admin_id")

    if (
        isinstance(admin_id, bool)
        or not isinstance(admin_id, int)
        or admin_id <= 0
    ):
        raise HTTPException(
            status_code=401,
            detail="Administrator login required",
        )

    with sqlite3.connect(db_path, timeout=10) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT id, role, active, deleted_at,
                   must_change_password
            FROM admins
            WHERE id = ?
            """,
            (admin_id,),
        ).fetchone()

    if admin is None:
        raise HTTPException(
            status_code=403,
            detail="Administrator access denied",
        )

    if (
        admin["role"] != "super_admin"
        or admin["active"] != 1
        or admin["deleted_at"] is not None
        or admin["must_change_password"] != 0
    ):
        raise HTTPException(
            status_code=403,
            detail="Super Admin access required",
        )

    return int(admin["id"])


# TAM-GALLERY-ADMIN-STATUS-V1

@router.get("/admin/gallery/status")
def gallery_admin_status(request: Request):
    """
    Return gallery configuration to an authenticated Super Admin.

    Read-only:
    - Never return the access-code hash.
    - Never expose S3 object keys.
    - Never change gallery settings.
    """

    require_gallery_super_admin(request, DB_PATH)

    event_id = SETTINGS.event_id

    with sqlite3.connect(DB_PATH, timeout=10) as connection:

        event = connection.execute(
            """
            SELECT gallery_enabled,
                   gallery_access_code_hash
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        if event is None:
            raise HTTPException(
                status_code=404,
                detail="Event not found",
            )

        approved = connection.execute(
            """
            SELECT COUNT(*)
            FROM photos
            WHERE event_id = ?
              AND gallery_approved = 1
            """,
            (event_id,),
        ).fetchone()[0]

        total = connection.execute(
            """
            SELECT COUNT(*)
            FROM photos
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()[0]

    return {
        "event_id": event_id,
        "gallery_enabled": bool(event[0]),
        "access_code_configured": bool(event[1]),
        "approved_photos": approved,
        "total_photos": total,
    }


# TAM-GALLERY-ADMIN-CSRF-V1

def verify_gallery_admin_csrf(
    request: Request,
    admin_id: int,
    submitted_token: str,
) -> None:
    """
    Verify a gallery administration request's CSRF token.

    Uses the same signing configuration as the existing
    TAM administrator CSRF protection.

    Raises HTTP 403 when verification fails.
    """
    import os

    from itsdangerous import (
        URLSafeTimedSerializer,
        BadSignature,
        SignatureExpired,
    )

    csrf_nonce = request.session.get("csrf_nonce", "")

    if (
        not isinstance(csrf_nonce, str)
        or not csrf_nonce
        or not isinstance(submitted_token, str)
        or not submitted_token
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token",
        )

    serializer = URLSafeTimedSerializer(
        os.environ["SESSION_SECRET"],
        salt="tam-admin-csrf",
    )

    try:
        data = serializer.loads(
            submitted_token,
            max_age=3600,
        )

        valid = (
            isinstance(data, dict)
            and type(data.get("admin_id")) is int
            and data["admin_id"] == admin_id
            and data.get("csrf_nonce") == csrf_nonce
        )

    except (BadSignature, SignatureExpired):
        valid = False

    if not valid:
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token",
        )


# TAM-GALLERY-ACCESS-CODE-ENDPOINT-V1

@router.post("/admin/gallery/access-code")
def set_gallery_access_code(
    request: Request,
    gallery_access_code: str = __import__("fastapi").Form(...),
    csrf_token: str = __import__("fastapi").Form(...),
):
    """
    Configure the event gallery access code.

    Super Admin and CSRF verification are mandatory.
    Never store or return the plaintext access code.
    Setting the code does not enable gallery sharing.
    """
    from fastapi.responses import JSONResponse
    from gallery_access_code import hash_gallery_access_code

    admin_id = require_gallery_super_admin(request, DB_PATH)
    verify_gallery_admin_csrf(request, admin_id, csrf_token)

    try:
        code_hash = hash_gallery_access_code(gallery_access_code)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Access code must contain 12–128 characters",
        )

    event_id = SETTINGS.event_id

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        cursor = connection.execute(
            """
            UPDATE events
            SET gallery_access_code_hash = ?
            WHERE event_id = ?
            """,
            (code_hash, event_id),
        )

        if cursor.rowcount != 1:
            raise HTTPException(
                status_code=404,
                detail="Event not found",
            )

        connection.commit()

    return JSONResponse(
        content={
            "success": True,
            "message": "Gallery access code updated",
            "gallery_enabled_changed": False,
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# TAM-GALLERY-ENABLE-ENDPOINT-V1

@router.post("/admin/gallery/enable")
def enable_event_gallery(
    request: Request,
    csrf_token: str = __import__("fastapi").Form(...),
    confirmation: str = __import__("fastapi").Form(...),
):
    """
    Enable gallery browsing for the configured event.

    Security:
    - Active Super Admin required.
    - Valid administrator CSRF token required.
    - Explicit confirmation required.
    - Gallery access code must already be configured.
    - Does not approve any photographs.
    """

    from fastapi.responses import JSONResponse

    admin_id = require_gallery_super_admin(
        request,
        DB_PATH,
    )

    verify_gallery_admin_csrf(
        request,
        admin_id,
        csrf_token,
    )

    if confirmation != "ENABLE":
        raise HTTPException(
            status_code=400,
            detail="Explicit ENABLE confirmation required",
        )

    event_id = SETTINGS.event_id


    # TAM-GALLERY-ENABLE-HASH-VALIDATION-V2
    from gallery_access_code import (
        is_valid_gallery_access_code_hash,
    )

    with sqlite3.connect(DB_PATH, timeout=10) as connection:

        # Lock the transaction before checking gallery settings.
        # This prevents another writer changing the access code
        # between validation and activation.
        connection.execute("BEGIN IMMEDIATE")

        event = connection.execute(
            """
            SELECT gallery_access_code_hash
            FROM events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        if event is None:
            raise HTTPException(
                status_code=404,
                detail="Event not found",
            )

        if not is_valid_gallery_access_code_hash(event[0]):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Gallery cannot be enabled. "
                    "Configure a valid event access code first."
                ),
            )

        cursor = connection.execute(
            """
            UPDATE events
            SET gallery_enabled = 1
            WHERE event_id = ?
            """,
            (event_id,),
        )

        if cursor.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail="Gallery activation failed",
            )

        connection.commit()

    return JSONResponse(
        content={
            "success": True,
            "gallery_enabled": True,
            "photos_automatically_approved": False,
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# TAM-GALLERY-DISABLE-ENDPOINT-V1

@router.post("/admin/gallery/disable")
def disable_event_gallery(
    request: Request,
    csrf_token: str = __import__("fastapi").Form(...),
    confirmation: str = __import__("fastapi").Form(...),
):
    """
    Disable event gallery access.

    Requires an active Super Admin and valid CSRF token.

    Does not delete photographs or change individual approvals.
    """

    from fastapi.responses import JSONResponse

    admin_id = require_gallery_super_admin(
        request,
        DB_PATH,
    )

    verify_gallery_admin_csrf(
        request,
        admin_id,
        csrf_token,
    )

    if confirmation != "DISABLE":
        raise HTTPException(
            status_code=400,
            detail="Explicit DISABLE confirmation required",
        )

    event_id = SETTINGS.event_id

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        cursor = connection.execute(
            """
            UPDATE events
            SET gallery_enabled = 0
            WHERE event_id = ?
            """,
            (event_id,),
        )

        if cursor.rowcount != 1:
            raise HTTPException(
                status_code=404,
                detail="Event not found",
            )

        connection.commit()

    return JSONResponse(
        content={
            "success": True,
            "gallery_enabled": False,
            "photos_deleted": False,
            "photo_approvals_changed": False,
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# TAM-GALLERY-PHOTO-APPROVAL-V1

@router.post("/admin/gallery/photos/{photo_id}/approve")
def approve_gallery_photo(
    photo_id: int,
    request: Request,
    csrf_token: str = __import__("fastapi").Form(...),
    confirmation: str = __import__("fastapi").Form(...),
):
    """
    Approve one eligible event photograph for gallery sharing.

    Requires an active Super Admin, valid CSRF token,
    and explicit confirmation.

    Does not access or modify the original S3 photograph.
    """

    from fastapi.responses import JSONResponse

    admin_id = require_gallery_super_admin(
        request,
        DB_PATH,
    )

    verify_gallery_admin_csrf(
        request,
        admin_id,
        csrf_token,
    )

    if confirmation != "APPROVE":
        raise HTTPException(
            status_code=400,
            detail="Explicit APPROVE confirmation required",
        )

    if photo_id <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid photograph ID",
        )

    event_id = SETTINGS.event_id

    with sqlite3.connect(DB_PATH, timeout=10) as connection:

        cursor = connection.execute(
            """
            UPDATE photos
            SET gallery_approved = 1
            WHERE id = ?
              AND event_id = ?
              AND status = 'available'
              AND s3_bucket = 'tam-smart-event-photos'
              AND s3_key IS NOT NULL
              AND s3_key != ''
            """,
            (photo_id, event_id),
        )

        if cursor.rowcount != 1:
            raise HTTPException(
                status_code=404,
                detail="Eligible photograph not found",
            )

        connection.commit()

    return JSONResponse(
        content={
            "success": True,
            "photo_id": photo_id,
            "gallery_approved": True,
            "original_photo_modified": False,
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# TAM-GALLERY-PHOTO-REVOKE-V1

@router.post("/admin/gallery/photos/{photo_id}/revoke")
def revoke_gallery_photo(
    photo_id: int,
    request: Request,
    csrf_token: str = __import__("fastapi").Form(...),
    confirmation: str = __import__("fastapi").Form(...),
):
    """
    Revoke gallery approval for one TAM event photograph.

    Requires active Super Admin authentication,
    valid CSRF token, and explicit REVOKE confirmation.

    Does not delete or modify the original S3 photograph.
    """

    from fastapi.responses import JSONResponse

    admin_id = require_gallery_super_admin(
        request,
        DB_PATH,
    )

    verify_gallery_admin_csrf(
        request,
        admin_id,
        csrf_token,
    )

    if confirmation != "REVOKE":
        raise HTTPException(
            status_code=400,
            detail="Explicit REVOKE confirmation required",
        )

    if photo_id <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid photograph ID",
        )

    event_id = SETTINGS.event_id

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        cursor = connection.execute(
            """
            UPDATE photos
            SET gallery_approved = 0
            WHERE id = ?
              AND event_id = ?
            """,
            (photo_id, event_id),
        )

        if cursor.rowcount != 1:
            raise HTTPException(
                status_code=404,
                detail="Event photograph not found",
            )

        connection.commit()

    return JSONResponse(
        content={
            "success": True,
            "photo_id": photo_id,
            "gallery_approved": False,
            "original_photo_modified": False,
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# TAM-GALLERY-ADMIN-PHOTO-LIST-V1

@router.get("/admin/gallery/photos")
def list_gallery_review_photos(
    request: Request,
    page: int = 1,
):
    """
    Return paginated event photographs for Super Admin review.

    Does not return private S3 object identifiers or
    contributor personal contact information.
    """

    from fastapi.responses import JSONResponse

    require_gallery_super_admin(
        request,
        DB_PATH,
    )

    if page < 1 or page > 100000:
        raise HTTPException(
            status_code=400,
            detail="Invalid page number",
        )

    event_id = SETTINGS.event_id
    page_size = 24
    offset = (page - 1) * page_size

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row

        total = connection.execute(
            """
            SELECT COUNT(*)
            FROM photos
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()[0]

        rows = connection.execute(
            """
            SELECT
                p.id,
                p.uploaded_at,
                p.status,
                p.gallery_approved,
                COALESCE(c.name, 'Unknown contributor')
                    AS contributor_name
            FROM photos AS p
            LEFT JOIN contributors AS c
                ON p.contributor_id = c.id
               AND p.event_id = c.event_id
            WHERE p.event_id = ?
            ORDER BY p.uploaded_at DESC, p.id DESC
            LIMIT ? OFFSET ?
            """,
            (event_id, page_size, offset),
        ).fetchall()

    photos = [
        {
            "id": row["id"],
            "uploaded_at": row["uploaded_at"],
            "status": row["status"],
            "gallery_approved": bool(
                row["gallery_approved"]
            ),
            "contributor_name": row["contributor_name"],
        }
        for row in rows
    ]

    return JSONResponse(
        content={
            "event_id": event_id,
            "page": page,
            "page_size": page_size,
            "total_photos": total,
            "total_pages": (
                (total + page_size - 1) // page_size
            ),
            "photos": photos,
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# TAM-GALLERY-ADMIN-THUMBNAIL-V1

@router.get("/admin/gallery/photos/{photo_id}/thumbnail")
def admin_gallery_photo_thumbnail(
    photo_id: int,
    request: Request,
):
    """
    Return a private, watermarked photo-review thumbnail.

    Only active Super Admins can access this endpoint.

    Unlike the attendee gallery, an administrator may preview
    unapproved photographs before deciding whether to share them.
    """

    from fastapi.responses import Response
    from aws_config import get_tam_s3_client
    from photo_watermark import create_guest_thumbnail

    event_id = SETTINGS.event_id

    # Initial authorization.
    admin_id = require_gallery_super_admin(
        request,
        DB_PATH,
    )

    if photo_id <= 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid photograph ID",
        )

    # Retrieve only the storage information required internally.
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row

        photo = connection.execute(
            """
            SELECT
                p.id,
                p.s3_bucket,
                p.s3_key,
                COALESCE(c.name, 'Unknown contributor')
                    AS contributor_name
            FROM photos AS p
            LEFT JOIN contributors AS c
                ON c.id = p.contributor_id
               AND c.event_id = p.event_id
            WHERE p.id = ?
              AND p.event_id = ?
              AND p.status = 'available'
              AND p.s3_bucket = 'tam-smart-event-photos'
              AND p.s3_key IS NOT NULL
              AND p.s3_key != ''
            """,
            (photo_id, event_id),
        ).fetchone()

    if photo is None:
        raise HTTPException(
            status_code=404,
            detail="Photograph unavailable",
        )

    # Retrieve original photograph from private S3 storage.
    image_bytes = get_tam_s3_client().get_object(
        Bucket=photo["s3_bucket"],
        Key=photo["s3_key"],
    )["Body"].read()

    # Create a smaller watermarked preview.
    thumbnail = create_guest_thumbnail(
        image_bytes,
        photo["contributor_name"],
    )

    # Recheck administrator authorization after processing.
    current_admin_id = require_gallery_super_admin(
        request,
        DB_PATH,
    )

    if current_admin_id != admin_id:
        raise HTTPException(
            status_code=403,
            detail="Administrator access changed",
        )

    # Recheck photograph availability before returning image.
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        still_available = connection.execute(
            """
            SELECT 1
            FROM photos
            WHERE id = ?
              AND event_id = ?
              AND status = 'available'
              AND s3_bucket = ?
              AND s3_key = ?
            """,
            (
                photo_id,
                event_id,
                photo["s3_bucket"],
                photo["s3_key"],
            ),
        ).fetchone()

    if still_available is None:
        raise HTTPException(
            status_code=403,
            detail="Photograph availability changed",
        )

    return Response(
        content=thumbnail,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
