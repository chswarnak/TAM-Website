"""Super Admin screen actions for organizer (role 'admin') permissions."""

import logging
import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from admin_auth import require_super_admin
from database import DB_PATH

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/admin/organizers/{organizer_id}/permissions")
def set_organizer_permissions(
    organizer_id: int,
    request: Request,
    csrf_token: str = Form(...),
    can_approve_contributors: str = Form(""),
    can_manage_photos: str = Form(""),
):
    """Replace an organizer's permissions with the submitted checkboxes
    (an unchecked box revokes that permission)."""
    super_admin_id = require_super_admin(request, csrf_token)
    approve, photos = int(can_approve_contributors == "1"), int(can_manage_photos == "1")

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        organizer = connection.execute(
            "SELECT id FROM admins WHERE id = ? AND role = 'admin' "
            "AND deleted_at IS NULL", (organizer_id,)
        ).fetchone()
        if organizer is None:
            raise HTTPException(status_code=404, detail="Organizer not found")
        connection.execute(
            """
            INSERT INTO organizer_permissions
                (admin_id, can_approve_contributors, can_manage_photos)
            VALUES (?, ?, ?)
            ON CONFLICT(admin_id) DO UPDATE SET
                can_approve_contributors = excluded.can_approve_contributors,
                can_manage_photos = excluded.can_manage_photos
            """,
            (organizer_id, approve, photos)
        )

    logger.info("Super Admin %s set organizer %s permissions: approve=%d photos=%d",
                super_admin_id, organizer_id, approve, photos)
    return RedirectResponse(url="/admin/organizers", status_code=303)
