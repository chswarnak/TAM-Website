"""Admin page for authorizing uploaded photos for face indexing."""

import logging
import sqlite3

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import face_indexing
from admin_auth import create_csrf_token, require_admin_permission
from aws_config import get_tam_s3_client
from database import DB_PATH
from photo_watermark import create_guest_thumbnail
from settings import SETTINGS

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")

PERMISSION = "can_manage_photos"


@router.get("/admin/photos/face-indexing", response_class=HTMLResponse)
def face_indexing_page(request: Request):
    if not isinstance(request.session.get("admin_id"), int):
        return RedirectResponse(url="/admin/login", status_code=303)
    admin_id = require_admin_permission(request, PERMISSION)
    return templates.TemplateResponse(
        request=request, name="admin_face_indexing.html",
        context={"photos": face_indexing.photos_awaiting_indexing(),
                 "auto_enabled": face_indexing.auto_authorize_enabled(),
                 "csrf_token": create_csrf_token(
                     admin_id, request.session.get("csrf_nonce", ""))},
    )


@router.post("/admin/photos/face-indexing/authorize")
def authorize_photos(
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(...),
    photo_ids: list[str] = Form(default=[]),
    all_pending: str = Form(""),
):
    admin_id = require_admin_permission(request, PERMISSION, csrf_token)
    if all_pending:
        ids = [p["id"] for p in face_indexing.photos_awaiting_indexing()]
    else:
        if not all(value.isdigit() for value in photo_ids):
            raise HTTPException(status_code=400, detail="Invalid photo id")
        ids = [int(value) for value in photo_ids]

    logger.info("Admin %s authorized %d photo(s) for face indexing", admin_id, len(ids))
    background_tasks.add_task(face_indexing.authorize_by_admin, ids, admin_id)
    return RedirectResponse(url="/admin/photos/face-indexing", status_code=303)


@router.get("/admin/photos/face-indexing/{photo_id}/thumbnail")
def face_indexing_thumbnail(photo_id: int, request: Request):
    """Thumbnail for the review page; needs only can_manage_photos (the
    full-size preview stays Super Admin-only)."""
    require_admin_permission(request, PERMISSION)
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        photo = connection.execute(
            """
            SELECT p.s3_bucket, p.s3_key, c.name AS contributor_name
            FROM photos p JOIN contributors c ON c.id = p.contributor_id
            WHERE p.id = ? AND p.event_id = ? AND p.status = 'available'
              AND p.s3_bucket = 'tam-smart-event-photos' AND p.s3_key IS NOT NULL
            """,
            (photo_id, SETTINGS.event_id)
        ).fetchone()
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    try:
        original = get_tam_s3_client().get_object(
            Bucket=photo["s3_bucket"], Key=photo["s3_key"])["Body"].read()
    except (ClientError, BotoCoreError) as error:
        logger.exception("Could not read photo %s from S3", photo_id)
        raise HTTPException(status_code=503, detail="Photo unavailable") from error
    return Response(content=create_guest_thumbnail(original, photo["contributor_name"]),
                    media_type="image/jpeg",
                    headers={"Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff"})
