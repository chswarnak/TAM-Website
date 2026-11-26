
"""
TAM Smart Event Photo Sharing System.

HTML page routes for the event gallery.

This module does not activate the gallery automatically.
The main FastAPI application must explicitly include this router.
"""

import sqlite3
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

    with sqlite3.connect(DB_PATH) as connection:
        row = connection.execute("SELECT gallery_code_required FROM events WHERE event_id = ?", (EVENT_ID,)).fetchone()
    return templates.TemplateResponse(
        request=request,
        name="gallery_register.html",
        context={"gallery_code_required": row is None or bool(row[0])},
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

    # TAM-GALLERY-FIRST-REGISTRATION-CONSENT-V1
    # General gallery access requires its own registered session.
    # Selfie-search consent must not automatically grant gallery access.
    if viewer is None:
        return RedirectResponse(
            url="/gallery/register",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )

    return templates.TemplateResponse(
        request=request,
        name="gallery.html",
        context={},
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
