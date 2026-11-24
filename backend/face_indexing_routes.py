"""Admin page for authorizing uploaded photos for face indexing."""

import logging

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import face_indexing
from admin_auth import create_csrf_token, require_admin_permission

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
