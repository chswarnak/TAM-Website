
"""
TAM Smart Event Photo Sharing System.

HTML page routes for the event gallery.

This module does not activate the gallery automatically.
The main FastAPI application must explicitly include this router.
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import DB_PATH
from gallery_routes import (
    get_gallery_viewer,
    is_gallery_enabled,
)


router = APIRouter()
templates = Jinja2Templates(directory="templates")

EVENT_ID = "tam-diwali-2026"


@router.get("/gallery/register", response_class=HTMLResponse)
async def gallery_registration_page(request: Request):
    """
    Show the registration form only while gallery access is enabled.
    """

    if not is_gallery_enabled(DB_PATH, EVENT_ID):
        raise HTTPException(
            status_code=403,
            detail="Event gallery is currently unavailable",
        )

    return templates.TemplateResponse(
        request=request,
        name="gallery_register.html",
        context={},
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/gallery", response_class=HTMLResponse)
async def gallery_page(request: Request):
    """
    Display the gallery page only to authorized visitors.

    The gallery photo APIs independently enforce authorization
    and individual photograph approval.
    """

    if not is_gallery_enabled(DB_PATH, EVENT_ID):
        raise HTTPException(
            status_code=403,
            detail="Event gallery is currently unavailable",
        )

    viewer = get_gallery_viewer(
        request,
        DB_PATH,
        EVENT_ID,
    )

    if viewer is None:
        import sqlite3
        from gallery_sessions import create_gallery_session, GALLERY_SESSION_DURATION

        consent_id = getattr(request.state, "event_selfie_consent_id", None)

        with sqlite3.connect(DB_PATH, timeout=10) as connection:
            attendee = connection.execute(
                """
                SELECT attendee_name, attendee_mobile
                FROM attendee_consents
                WHERE id = ? AND event_id = ?
                  AND consent_type = 'selfie_search'
                  AND accepted_at IS NOT NULL AND withdrawn_at IS NULL
                """,
                (consent_id, EVENT_ID),
            ).fetchone()

            if attendee is None or not attendee[0] or not attendee[1]:
                return RedirectResponse(
                    url="/attendee-consent?entry=event",
                    status_code=303,
                    headers={"Cache-Control": "no-store"},
                )

            token = create_gallery_session(connection, EVENT_ID, attendee[0], attendee[1])

        response = RedirectResponse(
            url="/gallery", status_code=303, headers={"Cache-Control": "no-store"}
        )
        response.delete_cookie("tam_gallery_session", path="/api/gallery", secure=True)
        response.set_cookie(
            "tam_gallery_session",
            token,
            max_age=GALLERY_SESSION_DURATION,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response

    return templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={},
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
