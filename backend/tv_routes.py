
"""TAM Live TV authenticated projection endpoints."""

import sqlite3

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from gallery_admin import require_gallery_photo_admin
from aws_config import get_tam_s3_client

from tv_service import get_next_batch, get_idle_photos

from tv_images import (
    get_projection_image,
    ProjectionImageError,
)

router = APIRouter()

templates = Jinja2Templates(directory="templates")

DB_PATH = "/data/tam-website/database/smart_event.db"

EVENT_ID = "tam-diwali-2026"


NO_CACHE = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
}


def require_tv_admin(request):

    return require_gallery_photo_admin(
        request,
        DB_PATH
    )


def get_tv_settings():

    with sqlite3.connect(
        DB_PATH, timeout=10
    ) as conn:

        conn.row_factory = sqlite3.Row

        settings = conn.execute(
            """
            SELECT *
            FROM tv_event_settings
            WHERE event_id = ?
            """,
            (EVENT_ID,)
        ).fetchone()

    if not settings:

        raise HTTPException(
            status_code=503,
            detail="Live TV is not configured"
        )

    return dict(settings)


@router.get("/tv", response_class=HTMLResponse)
def live_tv_page(request: Request):

    require_tv_admin(request)

    return templates.TemplateResponse(
        request=request,
        name="live_tv.html",
        context={},
        headers=NO_CACHE
    )


@router.get("/api/tv/state")
def live_tv_state(request: Request):

    require_tv_admin(request)

    settings = get_tv_settings()

    if settings["enabled"] != 1:

        return JSONResponse(
            {
                "enabled": False,
                "mode": "blank",
                "photos": [],
                "idle_photos": [],
            },
            headers=NO_CACHE
        )

    # TAM-AUTOMATIC-TV-SYNC-V2

    from tv_sync import schedule_automatic_sync

    schedule_automatic_sync(DB_PATH)

    state = get_next_batch(
        DB_PATH,
        EVENT_ID
    )

    idle = []

    if state["mode"] == "slideshow":

        idle = get_idle_photos(
            DB_PATH,
            EVENT_ID
        )

    return JSONResponse(
        {
            "enabled": True,

            "mode": state["mode"],

            "photos": state.get(
                "photos", []
            )[:4],

            "idle_photos": idle,

            "ends_at": state.get("ends_at"),

            "ken_burns_enabled": bool(
                settings["ken_burns_enabled"]
            ),

            "auto_cycle_enabled": bool(
                settings["auto_cycle_enabled"]
            ),

            "slideshow_duration_seconds":
                settings["slideshow_duration_seconds"],


        },
        headers=NO_CACHE
    )


@router.get("/api/tv/photos/{photo_id}/image")
def live_tv_photo(
    photo_id: int,
    request: Request
):

    require_tv_admin(request)

    if photo_id <= 0:

        raise HTTPException(
            status_code=400,
            detail="Invalid photograph"
        )

    try:

        image = get_projection_image(
            DB_PATH,
            photo_id,
            get_tam_s3_client(),
            EVENT_ID
        )

    except ProjectionImageError:

        raise HTTPException(
            status_code=404,
            detail="Photograph unavailable for projection"
        )

    except Exception:

        raise HTTPException(
            status_code=503,
            detail="Projection image temporarily unavailable"
        )

    return Response(
        content=image,
        media_type="image/jpeg",
        headers=NO_CACHE
    )


# TAM-TV-HOST-ROUTES-V1

import secrets
import hmac

from tv_host import (
    get_host_status,
    update_host_setting,
    set_display_mode,
)


@router.get(
    "/admin/live-tv",
    response_class=HTMLResponse
)
def admin_live_tv_page(request: Request):

    require_tv_admin(request)

    token = request.session.get("tam_tv_csrf")

    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session["tam_tv_csrf"] = token

    return templates.TemplateResponse(
        request=request,
        name="admin_live_tv.html",
        context={"csrf_token": token},
        headers=NO_CACHE
    )


@router.get("/api/admin/tv/status")
def admin_live_tv_status(request: Request):

    require_tv_admin(request)

    return JSONResponse(
        get_host_status(DB_PATH, EVENT_ID),
        headers=NO_CACHE
    )


@router.post("/api/admin/tv/control")
async def admin_live_tv_control(request: Request):

    admin_id = require_tv_admin(request)

    expected = request.session.get("tam_tv_csrf")
    supplied = request.headers.get("X-TAM-TV-CSRF", "")

    if (
        not isinstance(expected, str)
        or not expected
        or not hmac.compare_digest(
            expected.encode("utf-8"),
            supplied.encode("utf-8")
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid security token"
        )

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid control request"
        )

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid control request"
        )

    try:

        from starlette.concurrency import run_in_threadpool

        if data.get("action") == "setting":

            result = await run_in_threadpool(
                update_host_setting,
                DB_PATH,
                admin_id,
                data.get("setting"),
                data.get("value"),
                EVENT_ID
            )

        elif data.get("action") == "mode":

            result = await run_in_threadpool(
                set_display_mode,
                DB_PATH,
                admin_id,
                data.get("mode"),
                EVENT_ID
            )

        else:
            raise ValueError("Invalid control action")

    except PermissionError:

        raise HTTPException(
            status_code=403,
            detail="Administrator access denied"
        )

    except (ValueError, TypeError):

        raise HTTPException(
            status_code=400,
            detail="Invalid Live TV control"
        )

    return JSONResponse(
        result,
        headers=NO_CACHE
    )
