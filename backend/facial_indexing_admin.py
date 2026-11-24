"""
TAM Smart Event Photo Sharing System
Super Admin Facial Indexing Review

Read-only administrative reporting.
No facial indexing or authorization changes.
"""

import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()

templates = Jinja2Templates(directory=str(
    Path(__file__).resolve().parent / "templates"
))

DB_FILE = Path("/data/tam-website/database/smart_event.db")
EVENT_ID = "tam-diwali-2026"
COLLECTION_ID = "tam-diwali-2026-faces"


@router.get("/admin/facial-indexing")
async def facial_indexing_review(request: Request):
    """Display indexing status to an authenticated Super Admin."""

    admin_id = request.session.get("admin_id")

    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    if not DB_FILE.is_file():
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )

    uri = DB_FILE.resolve().as_uri() + "?mode=ro"

    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")

        admin = conn.execute(
            """
            SELECT id, role, must_change_password
            FROM admins
            WHERE id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        if admin is None or admin["role"] != "super_admin":
            raise HTTPException(
                status_code=403,
                detail="Super Admin access required"
            )

        if admin["must_change_password"]:
            return RedirectResponse(
                url="/admin/change-password",
                status_code=303
            )

        stats = conn.execute(
            """
            SELECT
                COUNT(*) AS available_photos,

                COALESCE(SUM(CASE
                    WHEN a.photo_id IS NULL THEN 1 ELSE 0
                END), 0) AS missing_authorization,

                COALESCE(SUM(CASE
                    WHEN a.authorized = 0 THEN 1 ELSE 0
                END), 0) AS explicitly_denied,

                COALESCE(SUM(CASE
                    WHEN a.authorized = 1
                     AND a.consent_reference IS NOT NULL
                     AND TRIM(a.consent_reference) != ''
                    THEN 1 ELSE 0
                END), 0) AS authorized,

                COALESCE(SUM(CASE
                    WHEN j.status = 'completed'
                    THEN 1 ELSE 0
                END), 0) AS completed_jobs

            FROM photos p

            LEFT JOIN photo_face_authorizations a
              ON a.photo_id = p.id

            LEFT JOIN photo_index_jobs j
              ON j.photo_id = p.id
             AND j.collection_id = ?

            WHERE p.event_id = ?
              AND p.status = 'available'
            """,
            (COLLECTION_ID, EVENT_ID)
        ).fetchone()

        # TAM-PHOTO-REVIEW-LIST-V1
        # Read-only photograph review list.
        photo_rows = conn.execute(
            """
            SELECT
                p.id AS photo_id,

                CASE
                    WHEN a.photo_id IS NULL
                        THEN 'Pending review'
                    WHEN a.authorized = 0
                        THEN 'Not authorized'
                    WHEN a.authorized = 1
                         AND a.consent_reference IS NOT NULL
                         AND TRIM(a.consent_reference) != ''
                        THEN 'Authorized'
                    ELSE 'Authorization incomplete'
                END AS authorization_status,

                COALESCE(j.status, 'Not indexed')
                    AS indexing_status

            FROM photos p

            LEFT JOIN photo_face_authorizations a
              ON a.photo_id = p.id

            LEFT JOIN photo_index_jobs j
              ON j.photo_id = p.id
             AND j.collection_id = ?

            WHERE p.event_id = ?
              AND p.status = 'available'

            ORDER BY p.id DESC
            LIMIT 25
            """,
            (COLLECTION_ID, EVENT_ID)
        ).fetchall()

    context = dict(stats)
    context["photo_rows"] = [dict(row) for row in photo_rows]

    return templates.TemplateResponse(
        request=request,
        name="admin_facial_indexing.html",
        context=context
    )
