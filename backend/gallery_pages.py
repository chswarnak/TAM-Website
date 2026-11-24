
"""
TAM Smart Event Photo Sharing System.

HTML page routes for the event gallery.

"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import DB_PATH
from settings import SETTINGS
from gallery_routes import (
    get_gallery_viewer,
    is_gallery_enabled,
)


router = APIRouter()
templates = Jinja2Templates(directory="templates")

EVENT_ID = SETTINGS.event_id


@router.get("/gallery/register")
async def gallery_registration_page():
    """The anonymous registration form is gone; viewers log in instead."""
    return RedirectResponse(url="/attendee/login", status_code=303)


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
        return RedirectResponse(
            url="/attendee/login",
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
