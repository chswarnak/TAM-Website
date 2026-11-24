from fastapi.staticfiles import StaticFiles
from database import DB_PATH
from settings import SETTINGS
import sqlite3
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi import Form, HTTPException
from starlette.middleware.sessions import SessionMiddleware
from itsdangerous import URLSafeTimedSerializer
from pydantic import BaseModel
from argon2 import PasswordHasher
from s3_storage import upload_photo_to_s3
from aws_config import save_tam_aws_credentials, get_tam_aws_session, get_tam_s3_client


import os
import secrets
from botocore.exceptions import BotoCoreError, ClientError

import boto3
from botocore.config import Config

from aws_config import (
    save_tam_aws_credentials,
    get_tam_aws_session,
    get_tam_s3_client
)



from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from dotenv import load_dotenv
import logging

logging.basicConfig(
    level=SETTINGS.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("tam")

# Errors an AWS credential/bucket check can raise (bad keys, network,
# missing configuration). Anything else is a bug and should surface.
AWS_CHECK_ERRORS = (ClientError, BotoCoreError, RuntimeError, ValueError, OSError)

load_dotenv()


app = FastAPI()

# TAM shared static assets (CSS, JavaScript, sound effects).
app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)


# TAM-GALLERY-ROUTERS-V1
# Gallery access remains controlled by database permissions.
from gallery_pages import router as gallery_pages_router
from gallery_routes import router as gallery_api_router
from gallery_admin import router as gallery_admin_router

app.include_router(gallery_pages_router)
app.include_router(gallery_api_router)
app.include_router(gallery_admin_router)

from account_routes import router as account_router
from attendee_routes import router as attendee_router
from admin_account_routes import router as admin_account_router

app.include_router(account_router)
app.include_router(attendee_router)
app.include_router(admin_account_router)



# Browser session configuration
import accounts
import rate_limit
from http_security import client_ip
from admin_auth import (
    SESSION_SECRET,
    authenticate_admin,
    create_csrf_token,
    has_admin_permission,
    password_hasher,
    require_admin_permission,
    verify_csrf_token,
)


app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="tam_admin_session",
    same_site="lax",
    https_only=os.environ.get("TAM_PRODUCTION") == "1",
    max_age=3600
)

# HTML templates configuration
templates = Jinja2Templates(directory="templates")


# Current event for local testing
EVENT_ID = SETTINGS.event_id

# TAM-DEV-UPLOAD-MODE-V1
# TEMPORARY: Allow uploads during application development.
# Set to False before the real event.
# Contributor authentication and approval remain mandatory.
DEV_UPLOADS_ANYTIME = False


def is_upload_window_open(event_id=EVENT_ID, check_at=None):
    # Allow testing without enforcing the event schedule.
    if DEV_UPLOADS_ANYTIME and event_id == EVENT_ID:
        return True

    from datetime import datetime, date, time
    from zoneinfo import ZoneInfo

    # Read the event settings from SQLite.
    with sqlite3.connect(DB_PATH) as connection:
        event = connection.execute(
            """
            SELECT event_date,
                   upload_start_time,
                   upload_end_time,
                   timezone,
                   status
            FROM events
            WHERE event_id = ?
            """,
            (event_id,)
        ).fetchone()

    # Unknown events cannot accept uploads.
    if event is None:
        return False

    event_date, start_time, end_time, timezone, status = event

    # Only active events can accept photos.
    if status != "active" or not event_date:
        return False

    # Check the event's local date and time.
    try:        
        now = (
            check_at.astimezone(ZoneInfo(timezone))
            if check_at is not None
            else datetime.now(ZoneInfo(timezone))
        )

        scheduled_date = date.fromisoformat(event_date)
        start = time.fromisoformat(start_time)
        end = time.fromisoformat(end_time)

        return (
            now.date() == scheduled_date
            and start <= now.time() < end
        )

    except (ValueError, KeyError):
        return False


@app.get("/")
def home():
    return RedirectResponse(
        url=f"/events/{EVENT_ID}",
        status_code=302
    )


from fastapi import UploadFile, File, Form, HTTPException
from pathlib import Path
from uuid import uuid4
from PIL import Image, UnidentifiedImageError
from io import BytesIO

UPLOAD_DIR = SETTINGS.upload_dir


@app.post("/upload")
async def upload_photo(
    request: Request,
    file: UploadFile = File(...)
):
    # Identify the contributor from their authenticated session.
    contributor_id = accounts.require_account(request, "contributor")["id"]

    # Enforce the event upload schedule on the server.
    if not is_upload_window_open(EVENT_ID):
        raise HTTPException(
            status_code=403,
            detail="Photo uploads are closed for this event"
        )

    # Maximum photo upload size: 100 MB.
    MAX_PHOTO_SIZE = 100 * 1024 * 1024

    data = await file.read(MAX_PHOTO_SIZE + 1)

    if len(data) > MAX_PHOTO_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Photo exceeds 100 MB limit"
        )


    # Verify the file is a supported image
    try:
        image = Image.open(BytesIO(data))
        image.verify()
        image_format = image.format
    except (UnidentifiedImageError, OSError):
        raise HTTPException(
            status_code=400,
            detail="Invalid image file"
        )

    extensions = {
        "JPEG": ".jpg",
        "PNG": ".png",
        "WEBP": ".webp"
    }

    if image_format not in extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format"
        )

    # Generate a unique filename
    filename = f"{uuid4()}{extensions[image_format]}"
    event_id = EVENT_ID
 
    # Verify contributor registration in SQLite
    with sqlite3.connect(DB_PATH) as connection:
        contributor = connection.execute(
            """
            
            SELECT id, name
            FROM contributors
            WHERE id = ?
                AND event_id = ?
                AND approved = 1
                AND uploads_enabled = 1
                AND active = 1
                AND deleted_at IS NULL

            """,
            (contributor_id, event_id)
        ).fetchone()

    if contributor is None:
        raise HTTPException(
            status_code=403,
            detail="Contributor is not approved or upload access is disabled"
        )

    # Folder names use only the numeric account id. Display names are
    # user-controlled text and must never become part of a path.
    contributor_folder = UPLOAD_DIR / event_id / f"contributor-{contributor[0]:03d}"
    contributor_folder.mkdir(parents=True, exist_ok=True)

    filepath = contributor_folder / filename

    
    # Save the photograph locally.
    filepath.write_bytes(data)

    s3_result = None

    try:
        # Upload the photograph to the correct
        # event and contributor folder in S3.
        s3_result = upload_photo_to_s3(
            event_id,
            contributor[0],
            filepath
        )

        # Register the local and S3 locations in SQLite.
        with sqlite3.connect(DB_PATH) as connection:
            cursor = connection.execute(
                """
                INSERT INTO photos (
                    event_id,
                    contributor_id,
                    filename,
                    file_path,
                    status,
                    s3_bucket,
                    s3_key
                )
                VALUES (?, ?, ?, ?, 'available', ?, ?)
                """,
                (
                    event_id,
                    contributor[0],
                    filename,
                    str(filepath),
                    s3_result["bucket"],
                    s3_result["key"]
                )
            )

            photo_id = cursor.lastrowid
            connection.commit()

    except (ClientError, BotoCoreError, OSError, sqlite3.Error, RuntimeError):
        logger.exception(
            "Upload failed for contributor %s (file %s, s3=%s)",
            contributor[0], filename, s3_result
        )
        # Remove the newly created local file
        # if S3 upload or database registration fails.
        filepath.unlink(missing_ok=True)

        # If S3 succeeded but database registration
        # failed, attempt to remove the S3 object too.
        if s3_result is not None:
            try:
                get_tam_s3_client().delete_object(
                    Bucket=s3_result["bucket"],
                    Key=s3_result["key"]
                )
            except (ClientError, BotoCoreError):
                # An orphaned S3 object must be investigated.
                logger.exception("S3 cleanup failed for %s", s3_result)

        raise HTTPException(
            status_code=503,
            detail="Photo could not be saved. Please try again."
        )

    return {
        "message": "Photo uploaded successfully",
        "photo_id": photo_id,
        "filename": filename,
        "status": "available",
        "s3_key": s3_result["key"]
    } 


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={}
    )


@app.post("/admin/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    ip = client_ip(request)
    wait = rate_limit.retry_after("admin", ip, username)
    if wait:
        response = templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={"error": "Too many failed attempts. Please wait and try again."},
            status_code=429
        )
        response.headers["Retry-After"] = str(wait)
        return response

    admin = authenticate_admin(username, password)

    if admin is None:
        rate_limit.record_failure("admin", ip, username)
        logger.warning("Failed admin login for %r from %s", username[:64], ip)
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={"error": "Invalid username or password"},
            status_code=401
        )

    rate_limit.clear_identifier("admin", ip, username)
    request.session.clear()
    request.session["admin_id"] = admin["id"]
    request.session["csrf_nonce"] = secrets.token_urlsafe(32)

    # New Admins must change their temporary password.
    if admin["must_change_password"]:
        return RedirectResponse(
            url="/admin/change-password",
            status_code=303
        )

    # Admins who completed password setup can use the dashboard.
    return RedirectResponse(
        url="/admin/dashboard",
        status_code=303
    )



@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    with sqlite3.connect(DB_PATH) as connection:
        admin = connection.execute(
            """
            SELECT name, role, must_change_password
            FROM admins
            WHERE id = ?
            AND active = 1
            AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

    if admin is None:
        request.session.clear()
        return RedirectResponse(
            url="/admin/login",
            status_code=303

        )  
    # Require new Admins to change their temporary password
    # before accessing the dashboard.
    if admin[2]:
        return RedirectResponse(
            url="/admin/change-password",
            status_code=303
        )

    # TAM-LIVE-DASHBOARD-CONTRIBUTORS-V1
    # Load current contributors from the production database.
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            """
            SELECT id, name, mobile_number, approved
            FROM contributors
            WHERE event_id = ?
              AND active = 1
              AND deleted_at IS NULL
            ORDER BY id DESC
            """,
            (EVENT_ID,)
        ).fetchall()

    contributors = []

    for row in rows:
        mobile = row["mobile_number"] or ""
        digits = "".join(c for c in mobile if c.isdigit())

        masked_mobile = (
            "****" + digits[-4:]
            if len(digits) >= 4
            else "Not provided"
        )

        contributors.append({
            "id": row["id"],
            "name": row["name"],
            "mobile": masked_mobile,
            "approved": bool(row["approved"])
        })

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "admin_name": admin[0],
            "admin_role": admin[1],
            "contributors": contributors
        }
    )


@app.post("/admin/logout")
async def admin_logout(request: Request):
    request.session.clear()

    response = RedirectResponse(
        url="/admin/login",
        status_code=303
    )
    response.headers["Cache-Control"] = "no-store"
    return response



@app.get("/admin/organizers", response_class=HTMLResponse)
async def manage_organizers(request: Request):
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT role, must_change_password
            FROM admins
            WHERE id = ? 
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        # Verify the account exists and has Super Admin privileges.
        if admin is None or admin["role"] != "super_admin":
            raise HTTPException(
                status_code=403,
                detail="Super Admin access required"
            )

        # Require password setup before accessing organizer management.
        if admin["must_change_password"]:
            return RedirectResponse(
                url="/admin/change-password",
                status_code=303
            )
        
        organizers = connection.execute(
            """
            SELECT id, name, username, role, active
            FROM admins
            WHERE deleted_at IS NULL
            ORDER BY id
            """
        ).fetchall()


    return templates.TemplateResponse(
        request=request,
        name="admin_manage_organizers.html",
        context={
            "organizers": organizers,
            "csrf_token": create_csrf_token(
                admin_id,
                request.session.get("csrf_nonce", "")
            )
        }
    )



@app.post("/admin/organizers/create")
async def create_organizer(
    request: Request,
    name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...)
):
    # Check that the user is logged in
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )
    
    # Verify that this request came from the current login session.
    csrf_nonce = request.session.get("csrf_nonce", "")

    if not verify_csrf_token(
        csrf_token,
        admin_id,
        csrf_nonce
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token"
        )

    

    with sqlite3.connect(DB_PATH) as connection:


        # Enforce TAM's maximum of 10 regular Admin accounts.
        # Disabled accounts count; deleted accounts do not.
        admin_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM admins
            WHERE role = 'admin'
              AND deleted_at IS NULL
            """
        ).fetchone()[0]

        if admin_count >= 10:
            raise HTTPException(
                status_code=400,
                detail="Maximum of 10 regular Admin accounts reached"
            )

        # Verify Super Admin permissions
        admin = connection.execute(
            """
            SELECT role
            FROM admins
            WHERE id = ? 
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        if admin is None or admin[0] != "super_admin":
            raise HTTPException(
                status_code=403,
                detail="Super Admin access required"
            )

        # Validate organizer details
        name = name.strip()
        username = username.strip().lower()

        if not name or not username or len(password) < 12:
            raise HTTPException(
                status_code=400,
                detail="Name, username, and a password of at least 12 characters are required"
            )

        # Check for an existing username
        existing = connection.execute(
            "SELECT id FROM admins WHERE username = ?",
            (username,)
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Username already exists"
            )

        # Store a password hash, never the password
        password_hash = password_hasher.hash(password)

        try:
            
            connection.execute(
                """
                INSERT INTO admins
                    (name, username, password_hash, role, active,
                    must_change_password)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    username,
                    password_hash,
                    "admin",
                    1,
                    1
                )
            )

            connection.commit()

        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=400,
                detail="Username already exists"
            )

    return RedirectResponse(
        url="/admin/organizers",
        status_code=303
    )


@app.post("/admin/organizers/{organizer_id}/disable")
async def disable_organizer(
    organizer_id: int,
    request: Request,
    csrf_token: str = Form(...)

):
    admin_id = request.session.get("admin_id")


    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )
    
    
    # Verify that the CSRF token belongs to the current login session.
    csrf_nonce = request.session.get("csrf_nonce", "")

    if not verify_csrf_token(
        csrf_token,
        admin_id,
        csrf_nonce
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token"
        )


    with sqlite3.connect(DB_PATH) as connection:
        current_admin = connection.execute(
            """
            SELECT role FROM admins
            WHERE id = ? 
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        if current_admin is None or current_admin[0] != "super_admin":
            raise HTTPException(
                status_code=403,
                detail="Super Admin access required"
            )

        cursor = connection.execute(
            """
            UPDATE admins
            SET active = 0
            WHERE id = ? AND role = 'admin'
            """,
            (organizer_id,)
        )

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Regular Admin not found"
            )

        connection.commit()

    return RedirectResponse(
        url="/admin/organizers",
        status_code=303
    )



@app.post("/admin/organizers/{organizer_id}/delete")
async def delete_organizer(
    organizer_id: int,
    request: Request,
    csrf_token: str = Form(...)
):
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )

    csrf_nonce = request.session.get("csrf_nonce", "")

    if not verify_csrf_token(
        csrf_token, admin_id, csrf_nonce
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token"
        )

    with sqlite3.connect(DB_PATH) as connection:

        # Only an active Super Admin can delete accounts.
        current_admin = connection.execute(
            """
            SELECT role
            FROM admins
            WHERE id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        if current_admin is None or current_admin[0] != "super_admin":
            raise HTTPException(
                status_code=403,
                detail="Super Admin access required"
            )

        # Soft-delete only regular Admin accounts.
        cursor = connection.execute(
            """
            UPDATE admins
            SET active = 0,
                deleted_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND role = 'admin'
              AND deleted_at IS NULL
            """,
            (organizer_id,)
        )

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Admin account not found"
            )

        connection.commit()

    return RedirectResponse(
        url="/admin/organizers",
        status_code=303
    )



@app.get("/admin/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):

    # Check whether the administrator is logged in.
    admin_id = request.session.get("admin_id")
    csrf_nonce = request.session.get("csrf_nonce", "")

    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    # Confirm the administrator account is still active.
    with sqlite3.connect(DB_PATH) as connection:
        admin = connection.execute(
            """
            SELECT must_change_password
            FROM admins
            WHERE id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

    if admin is None:
        request.session.clear()
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    # Administrators who have completed setup use the dashboard.
    if not admin[0]:
        return RedirectResponse(
            url="/admin/dashboard",
            status_code=303
        )

    # Require a session-specific CSRF value.
    if not csrf_nonce:
        request.session.clear()
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    # Display the password-change form.
    return templates.TemplateResponse(
        request=request,
        name="admin_change_password.html",
        context={
            "csrf_token": create_csrf_token(
                admin_id,
                csrf_nonce
            )
        }
    )

# Add the password-change endpoint

@app.post("/admin/change-password")
async def change_admin_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...)
):
    admin_id = request.session.get("admin_id")
    csrf_nonce = request.session.get("csrf_nonce", "")

    # Require an authenticated administrator.
    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )

    # Protect the password-change form against CSRF.
    if not verify_csrf_token(
        csrf_token, admin_id, csrf_nonce
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token"
        )

    # Validate the new password.
    if new_password != confirm_password:
        raise HTTPException(
            status_code=400,
            detail="New passwords do not match"
        )

    if len(new_password) < 12:
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 12 characters"
        )

    with sqlite3.connect(DB_PATH) as connection:
        admin = connection.execute(
            """
            SELECT password_hash, must_change_password
            FROM admins
            WHERE id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        if admin is None or not admin[1]:
            raise HTTPException(
                status_code=403,
                detail="Password change not required or account unavailable"
            )

        # Verify the existing temporary password.
        try:
            password_hasher.verify(admin[0], current_password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            raise HTTPException(
                status_code=400,
                detail="Incorrect temporary password"
            )

        
        
        # Prevent reusing the temporary password.
        try:
            same_password = password_hasher.verify(
                admin[0],
                new_password
            )
        except VerifyMismatchError:
            same_password = False

        if same_password:
            raise HTTPException(
                status_code=400,
                detail="Choose a different password"
            )


        # Save the new Argon2 password hash.
        connection.execute(
            """
            UPDATE admins
            SET password_hash = ?,
                must_change_password = 0
            WHERE id = ?
            """,
            (password_hasher.hash(new_password), admin_id)
        )
        connection.commit()

    # Require a fresh login after changing the password.
    request.session.clear()

    return RedirectResponse(
        url="/admin/login",
        status_code=303
    )

#Add the Contributor Management route
@app.get("/admin/contributors/manage", response_class=HTMLResponse)
async def manage_contributors(request: Request):
    admin_id = request.session.get("admin_id")

    # Require an authenticated administrator session.
    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        # Confirm this is an active administrator who
        # has completed any required password change.
        admin = connection.execute(
            """
            SELECT role, must_change_password
            FROM admins
            WHERE id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        if admin is None or admin["role"] not in ("super_admin", "admin"):
            raise HTTPException(
                status_code=403,
                detail="Administrator access required"
            )

        if admin["must_change_password"]:
            return RedirectResponse(
                url="/admin/change-password",
                status_code=303
            )

        # Retrieve contributors for the current TAM event.
        
        contributors = connection.execute(
            """
            SELECT
                c.id,
                c.name,
                c.email,
                c.username,
                c.mobile_number,
                c.approved,
                c.uploads_enabled,
                c.active,
                c.deleted_at,
                c.approved_at,
                a.name AS approved_by
            FROM contributors AS c
            LEFT JOIN admins AS a
                ON c.approved_by_admin_id = a.id
            WHERE c.event_id = ?
            AND c.active = 1
            AND c.deleted_at IS NULL
            ORDER BY c.id
            """,
            (EVENT_ID,)
        ).fetchall()



    return templates.TemplateResponse(
        request=request,
        name="admin_manage_contributors.html",
        context={
            "contributors": contributors,
            "csrf_token": create_csrf_token(
                admin_id,
                request.session.get("csrf_nonce", "")
            )
        }
    )

    
# Create the Disable Uploads endpoint

@app.post("/admin/contributors/{contributor_id}/disable-uploads")
def disable_contributor_uploads(
    contributor_id: int,
    request: Request,
    csrf_token: str = Form(...)
):
    # Check that the administrator is logged in and authorized.
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )

    if not has_admin_permission(
        admin_id,
        "can_approve_contributors"
    ):
        raise HTTPException(
            status_code=403,
            detail="Contributor management permission required"
        )

    # Verify CSRF protection.
    csrf_nonce = request.session.get("csrf_nonce", "")

    if not verify_csrf_token(
        csrf_token,
        admin_id,
        csrf_nonce
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token"
        )

    # Disable new uploads without touching existing photos.
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            UPDATE contributors
            SET uploads_enabled = 0
            WHERE id = ?
              AND event_id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (contributor_id, EVENT_ID)
        )
        connection.commit()

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Active contributor not found"
            )

    return RedirectResponse(
        url="/admin/contributors/manage",
        status_code=303
    )


@app.post("/admin/contributors/{contributor_id}/enable-uploads")
def enable_contributor_uploads(
    contributor_id: int,
    request: Request,
    csrf_token: str = Form(...)
):
    # Verify administrator login and permission.
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )

    if not has_admin_permission(
        admin_id,
        "can_approve_contributors"
    ):
        raise HTTPException(
            status_code=403,
            detail="Contributor management permission required"
        )

    # Verify the current login session's CSRF token.
    csrf_nonce = request.session.get("csrf_nonce", "")

    if not verify_csrf_token(
        csrf_token,
        admin_id,
        csrf_nonce
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token"
        )

    # Restore uploading only for approved, active contributors.
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            UPDATE contributors
            SET uploads_enabled = 1
            WHERE id = ?
              AND event_id = ?
              AND approved = 1
              AND active = 1
              AND deleted_at IS NULL
            """,
            (contributor_id, EVENT_ID)
        )
        connection.commit()

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Eligible contributor not found"
            )

    return RedirectResponse(
        url="/admin/contributors/manage",
        status_code=303
    )


@app.post("/admin/contributors/{contributor_id}/delete")
def delete_contributor(
    contributor_id: int,
    request: Request,
    csrf_token: str = Form(...)
):
    # Check the administrator's session.
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )

    # Verify contributor-management permission.
    if not has_admin_permission(
        admin_id,
        "can_approve_contributors"
    ):
        raise HTTPException(
            status_code=403,
            detail="Contributor management permission required"
        )

    # Verify the security token.
    csrf_nonce = request.session.get("csrf_nonce", "")

    if not verify_csrf_token(
        csrf_token,
        admin_id,
        csrf_nonce
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token"
        )

    # Soft-delete the contributor, preserving uploaded files.
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            UPDATE contributors
            SET active = 0,
                uploads_enabled = 0,
                deleted_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND event_id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (contributor_id, EVENT_ID)
        )
        connection.commit()

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Active contributor not found"
            )

    return RedirectResponse(
        url="/admin/contributors/manage",
        status_code=303
    )


def _contributor_page_guard(request: Request, need_uploads: bool):
    """Return (account, None) or (None, response) for contributor pages."""
    account = accounts.session_account(request, "contributor")
    if account is None:
        return None, RedirectResponse(url="/contributor/login", status_code=303)
    if account["must_change_password"]:
        return None, RedirectResponse(url="/account/change-password", status_code=303)
    if need_uploads and not account["uploads_enabled"]:
        raise HTTPException(status_code=403,
                            detail="Photo uploads are disabled for your account")
    return account, None


@app.get("/contributor/upload", response_class=HTMLResponse)
async def contributor_upload_page(request: Request):
    account, redirect = _contributor_page_guard(request, need_uploads=True)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request=request, name="contributor_upload.html", context={}
    )

@app.get("/admin/events", response_class=HTMLResponse)
async def manage_events(request: Request):

    # Require Super Admin login.
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        # Confirm the account is an active Super Admin.
        admin = connection.execute(
            """
            SELECT role, must_change_password
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

        # Load current event settings.
        event = connection.execute(
            """
            SELECT *
            FROM events
            WHERE event_id = ?
            """,
            (EVENT_ID,)
        ).fetchone()

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    return templates.TemplateResponse(
        request=request,
        name="admin_manage_events.html",
        context={
            "event": event,
            "csrf_token": create_csrf_token(
                admin_id,
                request.session.get("csrf_nonce", "")
            )
        }
    )


@app.get("/admin/aws", response_class=HTMLResponse)
async def manage_aws(request: Request):

    # Require Admin login.
    admin_id = request.session.get("admin_id")
    
    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    # Create CSRF nonce for logged-in Admin.
    if not request.session.get("csrf_nonce"):
        request.session["csrf_nonce"] = secrets.token_urlsafe(32)

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        # Confirm active Super Admin access.
        admin = connection.execute(
            """
            SELECT role, must_change_password
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
        
    
    # TAM-AWS-IAM-STATUS-V2
    # Report authentication independently from S3 permissions.
    # Never expose AWS account IDs or credentials.

    aws_status = "AWS authentication unavailable"
    aws_identity = "Credential source not verified"

    try:
        session = get_tam_aws_session()
        credentials = session.get_credentials()

        provider = (
            credentials.method
            if credentials is not None
            else "unknown"
        )

        sts = session.client(
            "sts",
            config=Config(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 1}
            )
        )

        identity = sts.get_caller_identity()
        arn = identity.get("Arn", "")

        if provider == "iam-role" and ":assumed-role/" in arn:
            role_name = (
                arn.split(":assumed-role/", 1)[1]
                .split("/", 1)[0]
            )
            aws_identity = "EC2 IAM Role: " + role_name

        elif provider == "iam-role":
            aws_identity = "EC2 IAM Role"

        else:
            aws_identity = (
                "AWS credential provider: " + provider
            )

        # STS authentication succeeded.
        aws_status = "AWS authenticated"

        # Check S3 separately.
        try:
            s3_client = get_tam_s3_client()

            s3_client.head_bucket(
                Bucket="tam-smart-event-photos"
            )

            aws_status = (
                "AWS authenticated | S3 bucket check: successful"
            )

        except (ClientError, BotoCoreError) as s3_error:
            logger.warning("S3 bucket check failed: %s", s3_error)
            error_code = ""

            if hasattr(s3_error, "response"):
                error_code = str(
                    s3_error.response
                    .get("Error", {})
                    .get("Code", "")
                )

            if error_code in ("403", "AccessDenied"):
                aws_status = (
                    "AWS authenticated | "
                    "S3 bucket check: access denied (403)"
                )

            elif error_code in ("404", "NoSuchBucket"):
                aws_status = (
                    "AWS authenticated | S3 bucket not found"
                )

            else:
                aws_status = (
                    "AWS authenticated | S3 bucket check: unavailable"
                )

    except AWS_CHECK_ERRORS as aws_error:
        logger.warning("AWS authentication check failed: %s", aws_error)
        aws_status = "AWS authentication unavailable"
        aws_identity = "Credential source not verified"


    return templates.TemplateResponse(
        request=request,
        name="admin_aws_config.html",
        context={
            "aws_status": aws_status,
            "aws_identity": aws_identity,
            "csrf_token": create_csrf_token(
                admin_id,
                request.session.get("csrf_nonce", "")
            )
        }
    )


@app.post("/admin/aws/save", response_class=HTMLResponse)
async def save_aws_settings(request: Request):

    admin_id = request.session.get("admin_id")

    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT role, must_change_password
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

    form = await request.form()


    submitted_token = str(form.get("csrf_token", ""))
    session_nonce = request.session.get("csrf_nonce", "")



    if not verify_csrf_token(
        str(form.get("csrf_token", "")),
        admin_id,
        request.session.get("csrf_nonce", "")
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid security token"
        )
    
    # Credential updates are allowed only from
    # the local development computer.
    if request.client is None or request.client.host not in (
        "127.0.0.1", "::1"
    ):
        raise HTTPException(
            status_code=403,
            detail="Local administration only"
        )

    access_key = str(
        form.get("aws_access_key", "")
    ).strip()

    secret_key = str(
        form.get("aws_secret_key", "")
    ).strip()

    session_token = str(
        form.get("aws_session_token", "")
    ).strip()

    aws_status = "Not connected"
    aws_message = "AWS credential verification failed."

    if not access_key or not secret_key:
        aws_message = "Both AWS keys are required."

    else:
        try:
            # Use ONLY the entered credentials.
            # Never fall back to personal environment keys.
            test_session = boto3.Session(
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=session_token or None,
                region_name="ca-central-1"
            )

            test_config = Config(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 1}
            )

            # Verify AWS authentication.
            sts = test_session.client(
                "sts",
                config=test_config
            )

            sts.get_caller_identity()

            # Verify access to TAM's S3 bucket.
            s3_test = test_session.client(
                "s3",
                config=test_config
            )

            s3_test.head_bucket(
                Bucket="tam-smart-event-photos"
            )

            # Credentials passed verification.
            # Replace previous entries in TAM .env.
            save_tam_aws_credentials(
                access_key,
                secret_key,
                session_token
            )

            aws_status = "Connected to TAM S3"
            aws_message = (
                "SUCCESS: AWS credentials verified "
                "and saved to TAM configuration."
            )

        except AWS_CHECK_ERRORS:
            # Log the cause for the operator, but do not expose AWS error
            # details or credential values to the browser.
            logger.exception("Verifying or saving AWS credentials failed")
            aws_message = (
                "Unable to verify or save AWS credentials. "
                "Please check the keys and permissions."
            )

    return templates.TemplateResponse(
        request=request,
        name="admin_aws_config.html",
        context={
            "aws_status": aws_status,
            "aws_identity": (
                "TAM .env configuration"
                if aws_status == "Connected to TAM S3"
                else "Not updated"
            ),
            "aws_message": aws_message,
            "csrf_token": create_csrf_token(
                admin_id,
                request.session.get("csrf_nonce", "")
            )
        }
    )

@app.post("/admin/events/save")
async def save_event_settings(
    request: Request,
    event_name: str = Form(...),
    event_date: str = Form(...),
    upload_start_time: str = Form(...),
    upload_end_time: str = Form(...),
    status: str = Form(...),
    retention_hours: int = Form(...),
    csrf_token: str = Form(...)
):
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Super Admin login required"
        )

    # Verify active Super Admin access.
    with sqlite3.connect(DB_PATH) as connection:
        admin = connection.execute(
            """
            SELECT role, must_change_password
            FROM admins
            WHERE id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

    if admin is None or admin[0] != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Super Admin access required"
        )

    if admin[1]:
        raise HTTPException(
            status_code=403,
            detail="Password change required"
        )

    # Verify CSRF protection.
    if not verify_csrf_token(
        csrf_token,
        admin_id,
        request.session.get("csrf_nonce", "")
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired security token"
        )

    # Validate event settings.
    event_name = event_name.strip()

    if not event_name or len(event_name) > 150:
        raise HTTPException(
            status_code=400,
            detail="Event name must contain 1–150 characters"
        )

    try:
        from datetime import date, time

        date.fromisoformat(event_date)
        start = time.fromisoformat(upload_start_time)
        end = time.fromisoformat(upload_end_time)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid event date or time"
        )

    if start >= end:
        raise HTTPException(
            status_code=400,
            detail="Upload end time must be after start time"
        )

    if status not in ("draft", "active", "completed"):
        raise HTTPException(
            status_code=400,
            detail="Invalid event status"
        )

    if not 1 <= retention_hours <= 168:
        raise HTTPException(
            status_code=400,
            detail="Retention must be between 1 and 168 hours"
        )

    # Save the settings.
    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute(
            """
            UPDATE events
            SET event_name = ?,
                event_date = ?,
                upload_start_time = ?,
                upload_end_time = ?,
                status = ?,
                retention_hours = ?
            WHERE event_id = ?
            """,
            (
                event_name,
                event_date,
                upload_start_time,
                upload_end_time,
                status,
                retention_hours,
                EVENT_ID
            )
        )
        connection.commit()

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Event not found"
            )

    return RedirectResponse(
        url="/admin/events",
        status_code=303
    )


@app.get("/contributor/camera", response_class=HTMLResponse)
async def contributor_camera_page(request: Request):
    account, redirect = _contributor_page_guard(request, need_uploads=True)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request=request, name="contributor_camera.html", context={}
    )

@app.get("/contributor/upload-status")
def contributor_upload_status():
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        event = connection.execute(
            """
            SELECT event_name, event_date,
                   upload_start_time, upload_end_time,
                   timezone, status
            FROM events
            WHERE event_id = ?
            """,
            (EVENT_ID,)
        ).fetchone()

    if event is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found"
        )

    return {
        "event_name": event["event_name"],
        "event_date": event["event_date"],
        "upload_start_time": event["upload_start_time"],
        "upload_end_time": event["upload_end_time"],
        "timezone": event["timezone"],
        "status": event["status"],
        "uploads_open": is_upload_window_open(EVENT_ID)
    }


@app.get("/contributor/dashboard", response_class=HTMLResponse)
async def contributor_dashboard(request: Request):
    account, redirect = _contributor_page_guard(request, need_uploads=False)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request=request,
        name="contributor_dashboard.html",
        context={
            "contributor_name": account["name"],
            "contributor_id": account["id"]
        }
    )

@app.get(
    f"/events/{EVENT_ID}",
    response_class=HTMLResponse
)
async def tam_event_portal(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="event_portal.html",
        context={}
    )



@app.get("/admin/api/consents")
async def admin_list_consents(request: Request):
    """
    Super Admin only.
    Retrieve TAM attendee consent records.
    """
    from fastapi.responses import JSONResponse

    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Admin login required"
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT role, must_change_password
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
            raise HTTPException(
                status_code=403,
                detail="Password change required"
            )

        rows = connection.execute(
            """
            SELECT
                id,
                event_id,
                attendee_name,
                consent_type,
                consent_version,
                accepted_at,
                withdrawn_at
            FROM attendee_consents
            ORDER BY accepted_at DESC, id DESC
            LIMIT 500
            """
        ).fetchall()

    records = []

    for row in rows:
        record = dict(row)

        record["status"] = (
            "withdrawn"
            if record["withdrawn_at"]
            else "accepted"
        )

        records.append(record)

    return JSONResponse({
        "event_id": EVENT_ID,
        "total_returned": len(records),
        "consents": records
    })


@app.get("/admin/consents", response_class=HTMLResponse)
async def admin_consent_dashboard(request: Request):
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        return RedirectResponse(
            url="/admin/login",
            status_code=303
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT role, must_change_password
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

    response = templates.TemplateResponse(
        request=request,
        name="admin_consents.html",
        context={}
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/admin/consents/export")
async def export_attendee_consents(request: Request):
    """
    Export TAM consent records as CSV.
    Super Admin access only.
    """
    import csv
    import io
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from fastapi.responses import Response

    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Admin login required"
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT role, must_change_password
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
            raise HTTPException(
                status_code=403,
                detail="Password change required"
            )

        records = connection.execute(
            """
            SELECT
                id,
                event_id,
                attendee_name,
                consent_type,
                consent_version,
                consent_text,
                accepted_at,
                withdrawn_at
            FROM attendee_consents
            ORDER BY id
            """
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Consent ID",
        "Event",
        "Attendee Name",
        "Consent Type",
        "Consent Version",
        "Exact Consent Statement",
        "Accepted At UTC",
        "Accepted At Montreal",
        "Withdrawn At UTC",
        "Status"
    ])

    def safe_csv(value):
        text = "" if value is None else str(value)

        # Prevent spreadsheet applications from
        # interpreting attendee input as formulas.
        if text.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + text

        return text

    for record in records:
        accepted = datetime.fromisoformat(
            record["accepted_at"]
        )

        accepted_local = accepted.astimezone(
            ZoneInfo("America/Toronto")
        ).isoformat()

        status = (
            "Withdrawn"
            if record["withdrawn_at"]
            else "Accepted"
        )

        writer.writerow([
            safe_csv(record["id"]),
            safe_csv(record["event_id"]),
            safe_csv(record["attendee_name"]),
            safe_csv(record["consent_type"]),
            safe_csv(record["consent_version"]),
            safe_csv(record["consent_text"]),
            safe_csv(record["accepted_at"]),
            safe_csv(accepted_local),
            safe_csv(record["withdrawn_at"]),
            status
        ])

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )

    filename = f"TAM_Consent_Records_{timestamp}.csv"

    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"
        }
    )


@app.get("/admin/photos/{photo_id}/preview")
async def admin_watermarked_photo_preview(
    photo_id: int,
    request: Request
):
    """Super Admin-only watermarked photo preview."""
    from fastapi.responses import Response
    from photo_watermark import add_contributor_watermark
    from aws_config import get_tam_s3_client

    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Admin login required"
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT role, must_change_password
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
            raise HTTPException(
                status_code=403,
                detail="Password change required"
            )

        photo = connection.execute(
            """
            SELECT
                p.id,
                p.s3_bucket,
                p.s3_key,
                c.name AS contributor_name
            FROM photos p
            JOIN contributors c
              ON c.id = p.contributor_id
            WHERE p.id = ?
              AND p.event_id = ?
              AND p.status = 'available'
            """,
            (photo_id, EVENT_ID)
        ).fetchone()

    if (
        photo is None
        or photo["s3_bucket"] != "tam-smart-event-photos"
        or not photo["s3_key"]
    ):
        raise HTTPException(
            status_code=404,
            detail="Photograph unavailable"
        )

    image_bytes = get_tam_s3_client().get_object(
        Bucket=photo["s3_bucket"],
        Key=photo["s3_key"]
    )["Body"].read()

    watermarked = add_contributor_watermark(
        image_bytes,
        photo["contributor_name"],
        "TAM Diwali 2026"
    )

    return Response(
        content=watermarked,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"
        }
    )
