"""Attendee consent, selfie search and matched-photo access.

Everything here requires a logged-in, admin-approved attendee account.
Biometric endpoints additionally require our exact origin and HTTPS.
"""

import logging
import sqlite3
from datetime import datetime, timezone

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

import accounts
import consent_service
import guest_sessions
from aws_config import get_tam_s3_client
from database import DB_PATH
from http_security import require_https, require_same_origin
from photo_watermark import add_contributor_watermark, create_guest_thumbnail
from rekognition_service import FaceCollectionMissingError, find_matching_event_photos
from settings import SETTINGS

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_SELFIE_BYTES = 5 * 1024 * 1024
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _page_guard(request: Request):
    """(account, None) for an approved attendee, else (None, redirect)."""
    account = accounts.session_account(request, "attendee")
    if account is None:
        return None, RedirectResponse(url="/attendee/login", status_code=303)
    if account["must_change_password"]:
        return None, RedirectResponse(url="/account/change-password", status_code=303)
    return account, None


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=10)


@router.get("/find-my-photos", response_class=HTMLResponse)
def find_my_photos_page(request: Request):
    account, redirect = _page_guard(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request=request, name="find_my_photos.html",
        context={"attendee_name": account["display_name"]},
    )


@router.get("/attendee-consent", response_class=HTMLResponse)
def attendee_consent_page(request: Request):
    account, redirect = _page_guard(request)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request=request, name="attendee_consent.html",
        context={"attendee_name": account["display_name"],
                 "consent_texts": consent_service.CONSENT_TEXTS},
    )


@router.get("/api/find-my-photos/consent-status")
def consent_status(request: Request):
    account = accounts.require_account(request, "attendee")
    consent_id = consent_service.active_consent_id(account["id"], "selfie_search")
    return JSONResponse({"consent_verified": consent_id is not None}, headers=NO_STORE)


@router.post("/api/attendee-consent")
def submit_consent(request: Request, photo_consent: str = Form(...)):
    """Record (agree) or withdraw (decline) the attendee's biometric consent."""
    account = accounts.require_account(request, "attendee")
    require_same_origin(request, strict=True)

    if photo_consent == "decline":
        consent_service.withdraw_consents(account["id"])
        with _connect() as conn:
            guest_sessions.revoke_attendee_sessions(conn, account["id"])
        request.session.pop(guest_sessions.SESSION_TOKEN_KEY, None)
        logger.info("Attendee %s declined/withdrew consent", account["id"])
        return {"status": "declined"}

    if photo_consent != "agree":
        raise HTTPException(status_code=400, detail="Please choose agree or decline.")

    records = [
        consent_service.record_attendee_consent(
            account["id"], account["display_name"], consent_type,
            account["mobile_number"],
        )
        for consent_type in ("facial_indexing", "selfie_search")
        if consent_service.active_consent_id(account["id"], consent_type) is None
    ]
    logger.info("Attendee %s gave consent (%d new record(s))", account["id"], len(records))
    return {"status": "recorded", "consents": records}


@router.post("/api/find-my-photos/search")
async def search_my_photos(
    request: Request,
    selfie: UploadFile = File(...),
    consent: str = Form(...),
):
    """Match a selfie against indexed event photos. The selfie is not stored."""
    account = accounts.require_account(request, "attendee")
    require_same_origin(request, strict=True)
    require_https(request)

    if consent != "yes":
        raise HTTPException(status_code=400, detail="Please confirm your consent.")
    consent_id = consent_service.active_consent_id(account["id"], "selfie_search")
    if consent_id is None:
        raise HTTPException(status_code=403,
                            detail="Please complete the consent form first.")

    selfie_bytes = await selfie.read(MAX_SELFIE_BYTES + 1)
    if len(selfie_bytes) > MAX_SELFIE_BYTES:
        raise HTTPException(status_code=413, detail="Selfie is too large.")

    with _connect() as conn:
        guest_sessions.revoke_attendee_sessions(conn, account["id"])
    request.session.pop(guest_sessions.SESSION_TOKEN_KEY, None)

    try:
        matches = find_matching_event_photos(selfie_bytes)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except (ClientError, BotoCoreError, FaceCollectionMissingError) as error:
        logger.exception("Selfie search failed for attendee %s", account["id"])
        raise HTTPException(status_code=503,
                            detail="Photo search is temporarily unavailable.") from error

    photo_ids = [m["photo_id"] for m in matches]
    if photo_ids:
        with _connect() as conn:
            token = guest_sessions.create_search_session(
                conn, SETTINGS.event_id, account["id"], consent_id, photo_ids)
        request.session[guest_sessions.SESSION_TOKEN_KEY] = token

    logger.info("Attendee %s search matched %d photo(s)", account["id"], len(photo_ids))
    return JSONResponse({"status": "success", "match_count": len(photo_ids)},
                        headers=NO_STORE)


def _photo_for_attendee(request: Request, photo_id: int) -> tuple[dict, sqlite3.Row]:
    account = accounts.require_account(request, "attendee")
    token = request.session.get(guest_sessions.SESSION_TOKEN_KEY)
    with _connect() as conn:
        photo = guest_sessions.accessible_photo(
            conn, token, SETTINGS.event_id, account["id"], photo_id)
    if photo is None:
        raise HTTPException(status_code=403, detail="Photo not available.")
    return account, photo


def _original_bytes(photo: sqlite3.Row) -> bytes:
    try:
        return get_tam_s3_client().get_object(
            Bucket=photo["s3_bucket"], Key=photo["s3_key"])["Body"].read()
    except (ClientError, BotoCoreError) as error:
        logger.exception("Could not read photo %s from S3", photo["id"])
        raise HTTPException(status_code=503, detail="Photo unavailable.") from error


@router.get("/api/guest/photos")
def list_my_photos(request: Request):
    account = accounts.require_account(request, "attendee")
    token = request.session.get(guest_sessions.SESSION_TOKEN_KEY)
    with _connect() as conn:
        photos = guest_sessions.list_accessible_photos(
            conn, token, SETTINGS.event_id, account["id"])
    return JSONResponse({"photos": photos}, headers=NO_STORE)


@router.get("/api/guest/photos/{photo_id}/thumbnail")
def my_photo_thumbnail(photo_id: int, request: Request):
    _, photo = _photo_for_attendee(request, photo_id)
    thumbnail = create_guest_thumbnail(_original_bytes(photo), photo["contributor_name"])
    return Response(content=thumbnail, media_type="image/jpeg", headers=NO_STORE)


@router.get("/api/guest/photos/{photo_id}/download")
def my_photo_download(photo_id: int, request: Request):
    account, photo = _photo_for_attendee(request, photo_id)
    watermarked = add_contributor_watermark(_original_bytes(photo),
                                            photo["contributor_name"])
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO guest_photo_downloads
                (event_id, consent_id, attendee_id, photo_id,
                 downloaded_at_utc, session_token_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (SETTINGS.event_id, photo["consent_id"], account["id"], photo_id,
             datetime.now(timezone.utc).isoformat(), photo["token_hash"])
        )
    return Response(
        content=watermarked, media_type="image/jpeg",
        headers={**NO_STORE,
                 "Content-Disposition": f'attachment; filename="TAM_Photo_{photo_id}.jpg"'},
    )
