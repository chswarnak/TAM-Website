from fastapi.staticfiles import StaticFiles
from database import DB_PATH
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



from dotenv import load_dotenv

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

app.include_router(gallery_pages_router)
app.include_router(gallery_api_router)



# Browser session configuration
SESSION_SECRET = os.environ["SESSION_SECRET"]


# Create a serializer for signed CSRF tokens.
csrf_serializer = URLSafeTimedSerializer(
    SESSION_SECRET,
    salt="tam-admin-csrf"
)



def create_csrf_token(admin_id: int, csrf_nonce: str) -> str:
    """Create a signed CSRF token for the current login session."""
    return csrf_serializer.dumps({
        "admin_id": admin_id,
        "csrf_nonce": csrf_nonce
    })


def verify_csrf_token(
    token: str,
    admin_id: int,
    csrf_nonce: str
) -> bool:
    """Verify the token belongs to the current login session."""
    from itsdangerous import BadSignature, SignatureExpired

    if not csrf_nonce:
        return False

    try:
        data = csrf_serializer.loads(
            token,
            max_age=3600
        )

        return (
            data.get("admin_id") == admin_id
            and data.get("csrf_nonce") == csrf_nonce
        )

    except (BadSignature, SignatureExpired):
        return False



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
EVENT_ID = "tam-diwali-2026"

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
    if status != "active":
        return False

    # Allow Diwali uploads through the end of the event.
    if event_id == EVENT_ID:
        montreal = ZoneInfo("America/Toronto")
        current_time = (
            check_at.astimezone(montreal)
            if check_at is not None
            else datetime.now(montreal)
        )
        if current_time < datetime(2026, 11, 28, 22, 0, tzinfo=montreal):
            return True

    if not event_date:
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


class ContributorRegistration(BaseModel):
    name: str
    email: str
    mobile_number: str
    password: str


class ContributorLogin(BaseModel):
    # TAM-CONTRIBUTOR-NAME-LOGIN-V1
    email: str | None = None
    name: str | None = None
    contributor_id: int | None = None
    password: str


# Verify administrator access
def verify_admin(provided_secret: str):
    expected_secret = os.getenv("ADMIN_SECRET")

    if not expected_secret:
        raise HTTPException(
            status_code=503,
            detail="Administrator authentication is not configured"
        )

    if not secrets.compare_digest(
        provided_secret,
        expected_secret
    ):
        raise HTTPException(
            status_code=403,
            detail="Administrator access denied"
        )

    return True


from argon2.exceptions import VerifyMismatchError, VerificationError

password_hasher = PasswordHasher()

def authenticate_admin(username: str, password: str):
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT id, name, username, password_hash, role, must_change_password
            FROM admins
            WHERE username = ?
            AND active = 1
            AND deleted_at IS NULL
            """,
            (username.strip().lower(),)
        ).fetchone()

    if admin is None:
        return None

    try:
        password_hasher.verify(
            admin["password_hash"],
            password
        )
    except (VerifyMismatchError, VerificationError):
        return None

    return {
        "id": admin["id"],
        "name": admin["name"],
        "username": admin["username"],
        "role": admin["role"],
        "must_change_password": bool(admin["must_change_password"])
    }


def has_admin_permission(admin_id: int, permission: str) -> bool:
    allowed_permissions = {
        "can_approve_contributors",
        "can_manage_photos"
    }

    if permission not in allowed_permissions:
        return False

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
        # Reject missing, disabled, or deleted administrators first.
        if admin is None:
            return False

        # Do not grant permissions until password setup is complete.
        if admin["must_change_password"]:
            return False

        if admin["role"] == "super_admin":
            return True

        if admin["role"] != "admin":
            return False

        permissions = connection.execute(
            """
            SELECT can_approve_contributors, can_manage_photos
            FROM organizer_permissions
            WHERE admin_id = ?
            """,
            (admin_id,)
        ).fetchone()

        if permissions is None:
            return False

        return bool(permissions[permission])





@app.get("/")
def home():
    return RedirectResponse(
        url="/events/tam-diwali-2026",
        status_code=302
    )


from fastapi import UploadFile, File, Form, HTTPException
from pathlib import Path
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from io import BytesIO
from pillow_heif import register_heif_opener

register_heif_opener()

UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)


@app.post("/upload")
async def upload_photo(
    request: Request,
    file: UploadFile = File(...)
):
    # Identify the contributor from their authenticated session.
    contributor_id = request.session.get("contributor_id")

    if contributor_id is None:
        raise HTTPException(
            status_code=401,
            detail="Contributor login required before uploading"
        )
    
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


    # Normalize supported photos to JPEG before storage and indexing.
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP", "HEIF", "MPO"}:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Detected photo format: {image.format or 'unknown'}. "
                        "Choose a JPEG, PNG, WebP, HEIC/HEIF, or MPO photo."
                    )
                )

            oriented = ImageOps.exif_transpose(image)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = oriented.convert("RGB")

            output = BytesIO()
            rgb.save(output, format="JPEG", quality=92)
            data = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(
            status_code=400,
            detail="Could not read this photo. Try exporting it as a JPEG."
        )

    if len(data) > MAX_PHOTO_SIZE:
        raise HTTPException(status_code=413, detail="Converted photo exceeds 100 MB")

    filename = f"{uuid4()}.jpg"
    event_id = "tam-diwali-2026"
 
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

    # Generate folder name using database information
    contributor_id = (
        f"contributor-{contributor[0]:03d}-"
        f"{contributor[1].lower()}"
    )


    contributor_folder = UPLOAD_DIR / event_id / contributor_id
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

    except Exception:
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
            except Exception:
                # A failed cleanup must be investigated.
                print("WARNING: S3 cleanup failed:", s3_result)

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


@app.post("/register")
def register_contributor(data: ContributorRegistration):
    name = data.name.strip()
    import re

    mobile = data.mobile_number.strip()
    email = data.email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(400, "Enter a valid email address.")
    password = data.password

    if len(password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 8 characters"
        )

    password_hash = password_hasher.hash(password)


    if not name or not mobile:
        raise HTTPException(
            status_code=400,
            detail="Name and mobile number are required"
        )

    # TAM-UNIQUE-CONTRIBUTOR-NAME-V1
    # Serialize registration writes to prevent concurrent duplicates.
    with sqlite3.connect(DB_PATH, timeout=15) as connection:
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            """
            SELECT id
            FROM contributors
            WHERE event_id = ?
              AND LOWER(TRIM(name)) = LOWER(TRIM(?))
            LIMIT 1
            """,
            (EVENT_ID, name)
        ).fetchone()

        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Contributor name already exists. "
                    "Please choose a different name."
                )
            )

        cursor = connection.execute(
        
            """
            INSERT INTO contributors
                (name, mobile_number, event_id, approved, password_hash, email)
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(event_id, email COLLATE NOCASE) DO NOTHING
            """,
            (name, mobile, EVENT_ID, password_hash, email)
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "An account already uses this email for this event.")
        contributor_id = cursor.lastrowid
        connection.commit()

    return {
        "message": "Registration submitted successfully",
        "contributor_id": contributor_id,
        "event_id": EVENT_ID,
        "status": "pending_approval"
    }


@app.post("/contributor/login")
def login_contributor(
    data: ContributorLogin,
    request: Request
):
    # Look up contributor by registered name.
    # Keep legacy ID login temporarily for compatibility.
    contributor = None

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        if data.email is not None and data.email.strip():
            contributor = connection.execute(
                """
                SELECT id, name, password_hash, approved, active, deleted_at
                FROM contributors
                WHERE event_id = ? AND email = ? COLLATE NOCASE
                """,
                (EVENT_ID, data.email.strip().lower()),
            ).fetchone()

        elif data.name is not None and data.name.strip():
            matches = connection.execute(
                """
                SELECT id, name, password_hash,
                       approved, active, deleted_at
                FROM contributors
                WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
                  AND event_id = ? AND email IS NULL
                LIMIT 2
                """,
                (data.name.strip(), EVENT_ID)
            ).fetchall()

            # Fail closed when names are ambiguous.
            if len(matches) == 1:
                contributor = matches[0]

        elif data.contributor_id is not None:
            contributor = connection.execute(
                """
                SELECT id, name, password_hash,
                       approved, active, deleted_at
                FROM contributors
                WHERE id = ? AND event_id = ? AND email IS NULL
                """,
                (data.contributor_id, EVENT_ID)
            ).fetchone()

    # Reject unknown accounts and accounts without passwords.
    if contributor is None or not contributor["password_hash"]:
        raise HTTPException(
            status_code=401,
            detail="Invalid contributor credentials"
        )

    # Verify the submitted password.
    try:
        password_hasher.verify(
            contributor["password_hash"],
            data.password
        )
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid contributor credentials"
        )

    # Check account status.
    if not contributor["active"] or contributor["deleted_at"]:
        raise HTTPException(
            status_code=403,
            detail="Contributor account is disabled"
        )

    if not contributor["approved"]:
        raise HTTPException(
            status_code=403,
            detail="Contributor registration is awaiting approval"
        )

    # Create an authenticated contributor session.
    request.session.clear()
    request.session["contributor_id"] = contributor["id"]

    return {
        "message": "Contributor login successful",
        "contributor_id": contributor["id"],
        "name": contributor["name"]
    }



from fastapi import Header


@app.post("/admin/approve/{contributor_id}")
def approve_contributor(
    contributor_id: int,
    request: Request,
    csrf_token: str = Form(...)
):
    # Identify the logged-in organizer
    admin_id = request.session.get("admin_id")

    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )

    
    # Verify the request belongs to the current login session.
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



    # Approve only a contributor for this event
    with sqlite3.connect(DB_PATH) as connection:
        
        cursor = connection.execute(
            """
            UPDATE contributors
            SET approved = 1,
                approved_by_admin_id = ?,
                approved_at = CURRENT_TIMESTAMP
            WHERE id = ?
            AND event_id = ?
            AND approved = 0
            AND active = 1
            AND deleted_at IS NULL
            """,
            (admin_id, contributor_id, EVENT_ID)
        )

              
        connection.commit()

        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Contributor not found for this event"
            )

        return RedirectResponse(
        url="/admin/contributors/manage",
        status_code=303
    )




@app.get("/admin/contributors")
def list_contributors(
    x_admin_secret: str = Header(default="")
):
    # Only authorized organizers can access this endpoint
    verify_admin(x_admin_secret)

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        contributors = connection.execute(
            """
            SELECT id, name, mobile_number, approved
            FROM contributors
            WHERE event_id = ?
            ORDER BY id
            """,
            (EVENT_ID,)
        ).fetchall()

    return {
        "event_id": EVENT_ID,
        "total_contributors": len(contributors),
        "contributors": [
            {
                "id": row["id"],
                "name": row["name"],
                "mobile_number": (
                    "****" + row["mobile_number"][-4:]
                    if row["mobile_number"] else None
                ),
                "status": (
                    "approved" if row["approved"] == 1
                    else "pending"
                )
            }
            for row in contributors
        ]
    }



@app.get("/admin/contributors/pending")
def list_pending_contributors(
    x_admin_secret: str = Header(default="")
):
    # Verify administrator access
    verify_admin(x_admin_secret)

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        pending = connection.execute(
            """
            SELECT id, name, mobile_number
            FROM contributors
            WHERE event_id = ? AND approved = 0
            ORDER BY id
            """,
            (EVENT_ID,)
        ).fetchall()

    return {
        "event_id": EVENT_ID,
        "total_pending": len(pending),
        "contributors": [
            {
                "id": row["id"],
                "name": row["name"],
                "mobile_number": (
                    "****" + row["mobile_number"][-4:]
                    if row["mobile_number"] else None
                ),
                "status": "pending"
            }
            for row in pending
        ]
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
    admin = authenticate_admin(username, password)

    if admin is None:
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={"error": "Invalid username or password"},
            status_code=401
        )

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
    print("CSRF session initialized:", bool(request.session.get("csrf_nonce")))

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
    print("CSRF session initialized:", bool(request.session.get("csrf_nonce")))


    if admin_id is None:
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )
    
    
    # Verify that the CSRF token belongs to the current login session.
    csrf_nonce = request.session.get("csrf_nonce", "")
    print(
        "Disable CSRF verification:",
        verify_csrf_token(csrf_token, admin_id, csrf_nonce)
    )

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
        except Exception:
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

    

def contributor_reset_signer():
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(
        SESSION_SECRET, salt="tam-contributor-password-reset"
    )


def contributor_password_fingerprint(password_hash):
    from hashlib import sha256

    return sha256(password_hash.encode("utf-8")).hexdigest()


@app.post("/admin/contributors/{contributor_id}/password-reset")
def create_contributor_password_reset(
    contributor_id: int, request: Request, csrf_token: str = Form(...)
):
    from secrets import token_urlsafe

    admin_id = request.session.get("admin_id")
    if admin_id is None or not has_admin_permission(admin_id, "can_approve_contributors"):
        raise HTTPException(403, "Contributor management permission required")

    nonce = request.session.get("csrf_nonce", "")
    if not nonce or not verify_csrf_token(csrf_token, admin_id, nonce):
        raise HTTPException(403, "Invalid or expired security token")

    with sqlite3.connect(DB_PATH) as connection:
        admin = connection.execute(
            """
            SELECT 1 FROM admins
            WHERE id = ? AND active = 1 AND deleted_at IS NULL
              AND must_change_password = 0
            """,
            (admin_id,),
        ).fetchone()
        if admin is None:
            raise HTTPException(403, "Active administrator access required")

        contributor = connection.execute(
            """
            SELECT password_hash FROM contributors
            WHERE id = ? AND event_id = ? AND active = 1 AND deleted_at IS NULL
            """,
            (contributor_id, EVENT_ID),
        ).fetchone()

    if not contributor or not contributor[0]:
        raise HTTPException(404, "Contributor account unavailable")

    token = contributor_reset_signer().dumps({
        "contributor": contributor_id,
        "event": EVENT_ID,
        "password": contributor_password_fingerprint(contributor[0]),
        "nonce": token_urlsafe(24),
    })
    reset_url = (
        "https://tam-smartphotos.duckdns.org/contributor/reset-password#"
        + token
    )
    return HTMLResponse(
        f"<h1>Contributor password reset</h1>"
        f"<p>Share this link privately with the verified contributor. "
        f"It expires in 30 minutes.</p>"
        f'<p><a href="{reset_url}">{reset_url}</a></p>'
        f'<p><a href="/admin/contributors/manage">Back to Manage Contributors</a></p>',
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@app.get("/contributor/reset-password", response_class=HTMLResponse)
def contributor_password_reset_page():
    return HTMLResponse(
        """
        <!doctype html>
        <html lang="en">
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Reset contributor password</title>
        <h1>Reset contributor password</h1>
        <p id="reset-help">Use the one-time link provided by your TAM event administrator.</p>
        <form id="reset-form" method="post" action="/contributor/reset-password" hidden>
            <input id="reset-token" name="token" type="hidden">
            <p><label>New password
                <input name="password" type="password" autocomplete="new-password"
                       required minlength="12" maxlength="128">
            </label></p>
            <p><label>Confirm new password
                <input name="confirmation" type="password" autocomplete="new-password"
                       required minlength="12" maxlength="128">
            </label></p>
            <button type="submit">Set New Password</button>
        </form>
        <p><a href="/contributor/login">Back to Contributor Login</a></p>
        <script>
            const token = location.hash.slice(1);
            if (token) {
                document.getElementById("reset-token").value = token;
                document.getElementById("reset-form").hidden = false;
                document.getElementById("reset-help").textContent =
                    "Choose a new password with at least 12 characters.";
                history.replaceState(null, "", location.pathname);
            }
        </script>
        </html>
        """,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@app.post("/contributor/reset-password")
def reset_contributor_password(
    token: str = Form(...), password: str = Form(...), confirmation: str = Form(...)
):
    from hmac import compare_digest
    from itsdangerous import BadData

    invalid = "This reset link is invalid, expired, or already used. Ask TAM for a new one."
    if len(token) > 1024:
        raise HTTPException(400, invalid)
    try:
        data = contributor_reset_signer().loads(token, max_age=30 * 60)
    except BadData:
        raise HTTPException(400, invalid)

    if data["event"] != EVENT_ID:
        raise HTTPException(400, invalid)
    if not 12 <= len(password) <= 128 or password != confirmation:
        raise HTTPException(400, "Enter matching passwords of 12 to 128 characters.")

    with sqlite3.connect(DB_PATH) as connection:
        current = connection.execute(
            """
            SELECT password_hash FROM contributors
            WHERE id = ? AND event_id = ? AND active = 1 AND deleted_at IS NULL
            """,
            (data["contributor"], EVENT_ID),
        ).fetchone()

        if not current or not current[0] or not compare_digest(
            contributor_password_fingerprint(current[0]), data["password"]
        ):
            raise HTTPException(400, invalid)

        cursor = connection.execute(
            """
            UPDATE contributors SET password_hash = ?
            WHERE id = ? AND event_id = ? AND active = 1 AND deleted_at IS NULL
              AND password_hash = ?
            """,
            (password_hasher.hash(password), data["contributor"], EVENT_ID, current[0]),
        )
        if cursor.rowcount != 1:
            raise HTTPException(400, invalid)

    return RedirectResponse(
        url="/contributor/login", status_code=303, headers={"Cache-Control": "no-store"}
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


@app.get("/contributor/register", response_class=HTMLResponse)
async def contributor_register_page(request: Request):
    # Display the contributor self-registration page.
    # Actual registration is handled by the existing POST /register API.
    return templates.TemplateResponse(
        request=request,
        name="contributor_register.html",
        context={}
    )


@app.get("/contributor/login", response_class=HTMLResponse)
async def contributor_login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="contributor_login.html",
        context={}
    )


@app.get("/contributor/upload", response_class=HTMLResponse)
async def contributor_upload_page(request: Request):

    # Check whether the contributor is logged in.
    contributor_id = request.session.get("contributor_id")

    if contributor_id is None:
        return RedirectResponse(
            url="/contributor/login",
            status_code=303
        )

    # Verify current contributor account permissions.
    with sqlite3.connect(DB_PATH) as connection:
        contributor = connection.execute(
            """
            SELECT id
            FROM contributors
            WHERE id = ?
              AND event_id = ?
              AND approved = 1
              AND uploads_enabled = 1
              AND active = 1
              AND deleted_at IS NULL
            """,
            (contributor_id, EVENT_ID)
        ).fetchone()

    if contributor is None:
        raise HTTPException(
            status_code=403,
            detail="Contributor is not authorized to upload photos"
        )

    return templates.TemplateResponse(
        request=request,
        name="contributor_upload.html",
        context={}
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

        except Exception as s3_error:
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

    except Exception:
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

    print(
        "AWS CSRF DIAGNOSTIC:",
        "form_token_present=", bool(submitted_token),
        "session_nonce_present=", bool(session_nonce),
        "admin_id_present=", bool(admin_id),
        "token_valid=", verify_csrf_token(
            submitted_token,
            admin_id,
            session_nonce
        ),
        flush=True
    )


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

        except Exception:
            # Do not expose AWS error details
            # or credential values to the browser.
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

    # Require contributor login.
    contributor_id = request.session.get("contributor_id")

    if contributor_id is None:
        return RedirectResponse(
            url="/contributor/login",
            status_code=303
        )

    # Check whether the contributor is approved and active.
    with sqlite3.connect(DB_PATH) as connection:
        contributor = connection.execute(
            """
            SELECT id
            FROM contributors
            WHERE id = ?
              AND event_id = ?
              AND approved = 1
              AND uploads_enabled = 1
              AND active = 1
              AND deleted_at IS NULL
            """,
            (contributor_id, EVENT_ID)
        ).fetchone()

    if contributor is None:
        raise HTTPException(
            status_code=403,
            detail="Contributor is not authorized to use Event Camera"
        )

    return templates.TemplateResponse(
        request=request,
        name="contributor_camera.html",
        context={}
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

    # Require contributor login.
    contributor_id = request.session.get("contributor_id")

    if contributor_id is None:
        return RedirectResponse(
            url="/contributor/login",
            status_code=303
        )

    # Check that the contributor is approved and active.
    with sqlite3.connect(DB_PATH) as connection:
        contributor = connection.execute(
            """
            SELECT id, name
            FROM contributors
            WHERE id = ?
              AND event_id = ?
              AND approved = 1
              AND active = 1
              AND deleted_at IS NULL
            """,
            (contributor_id, EVENT_ID)
        ).fetchone()

    if contributor is None:
        raise HTTPException(
            status_code=403,
            detail="Contributor account is not authorized"
        )

    return templates.TemplateResponse(
        request=request,
        name="contributor_dashboard.html",
        context={
            "contributor_name": contributor[1],
            "contributor_id": contributor[0]
        }
    )


@app.post("/contributor/logout")
async def contributor_logout(request: Request):

    # Clear the contributor's authenticated session.
    request.session.clear()

    return RedirectResponse(
        url="/contributor/login",
        status_code=303
    )


@app.get("/api/find-my-photos/consent-status")
async def guest_consent_status(request: Request):
    from fastapi.responses import JSONResponse
    from guest_sessions import (
        CONSENT_FLOW_COOKIE_NAME,
        CONSENT_FLOW_DURATION_SECONDS,
        create_guest_consent_flow,
        validate_guest_consent_flow,
    )

    consent_id = active_event_selfie_consent(request)
    response = JSONResponse(
        {"consent_verified": consent_id is not None},
        headers={"Cache-Control": "no-store"},
    )
    if consent_id is None:
        return response

    current_token = request.cookies.get(CONSENT_FLOW_COOKIE_NAME)
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        if validate_guest_consent_flow(connection, current_token, EVENT_ID) == consent_id:
            return response
        token = create_guest_consent_flow(connection, consent_id, EVENT_ID)

    response.set_cookie(
        CONSENT_FLOW_COOKIE_NAME,
        token,
        max_age=CONSENT_FLOW_DURATION_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@app.get("/api/guest/photos/{photo_id}/download")
async def guest_photo_download(photo_id: int, request: Request):
    """Download an authorized watermarked event photograph."""
    from datetime import datetime, timezone
    from fastapi.responses import Response
    from guest_sessions import (
        guest_can_access_photo,
        hash_guest_token,
    )
    from photo_watermark import add_contributor_watermark
    from aws_config import get_tam_s3_client

    guest_token = request.cookies.get("tam_guest_photos")

    if not guest_token:
        raise HTTPException(status_code=401, detail="Guest session required.")

    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.row_factory = sqlite3.Row

        if not guest_can_access_photo(
            conn, guest_token, EVENT_ID, photo_id
        ):
            raise HTTPException(status_code=403, detail="Photo not authorized.")

        session = conn.execute("""
            SELECT consent_id
            FROM guest_search_sessions
            WHERE token_hash = ?
              AND event_id = ?
              AND expires_at > CAST(strftime('%s', 'now') AS INTEGER)
        """, (hash_guest_token(guest_token), EVENT_ID)).fetchone()

        photo = conn.execute("""
            SELECT p.s3_bucket, p.s3_key, c.name AS contributor_name
            FROM photos p
            JOIN contributors c ON c.id = p.contributor_id
            WHERE p.id = ? AND p.event_id = ?
        """, (photo_id, EVENT_ID)).fetchone()

    if session is None or photo is None:
        raise HTTPException(status_code=403, detail="Photo unavailable.")

    if (
        photo["s3_bucket"] != "tam-smart-event-photos"
        or not photo["s3_key"]
    ):
        raise HTTPException(status_code=404, detail="Photo unavailable.")

    original_bytes = get_tam_s3_client().get_object(
        Bucket=photo["s3_bucket"],
        Key=photo["s3_key"]
    )["Body"].read()

    watermarked_bytes = add_contributor_watermark(
        original_bytes, photo["contributor_name"]
    )

    # Record each successfully prepared download response.
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        if not guest_can_access_photo(
            conn, guest_token, EVENT_ID, photo_id
        ):
            raise HTTPException(status_code=403, detail="Session expired.")

        with conn:
            conn.execute("""
                INSERT INTO guest_photo_downloads (
                    event_id,
                    consent_id,
                    photo_id,
                    downloaded_at_utc,
                    session_token_hash
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                EVENT_ID,
                session["consent_id"],
                photo_id,
                datetime.now(timezone.utc).isoformat(),
                hash_guest_token(guest_token)
            ))

    return Response(
        content=watermarked_bytes,
        media_type="image/jpeg",
        headers={
            "Content-Disposition": f'attachment; filename="TAM_Photo_{photo_id}.jpg"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"
        }
    )


@app.get("/api/guest/photos/{photo_id}/thumbnail")
async def guest_photo_thumbnail(
    photo_id: int,
    request: Request
):
    """
    Deliver an authorized watermarked thumbnail.
    Local development only.
    """
    from fastapi.responses import Response
    from guest_sessions import guest_can_access_photo
    from photo_watermark import create_guest_thumbnail
    from aws_config import get_tam_s3_client

    guest_token = request.cookies.get("tam_guest_photos")

    if not guest_token:
        raise HTTPException(
            status_code=401,
            detail="Guest photo session required."
        )

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.row_factory = sqlite3.Row

        if not guest_can_access_photo(
            connection, guest_token, EVENT_ID, photo_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Photograph not authorized."
            )

        photo = connection.execute("""
            SELECT
                p.s3_bucket,
                p.s3_key,
                c.name AS contributor_name
            FROM photos p
            JOIN contributors c
              ON c.id = p.contributor_id
            WHERE p.id = ?
              AND p.event_id = ?
              AND p.status = 'available'
        """, (photo_id, EVENT_ID)).fetchone()

    if photo is None:
        raise HTTPException(
            status_code=404,
            detail="Photograph unavailable."
        )

    # S3 remains private.
    if (
        photo["s3_bucket"] != "tam-smart-event-photos"
        or not photo["s3_key"]
    ):
        raise HTTPException(
            status_code=404,
            detail="Photograph unavailable."
        )

    image_bytes = get_tam_s3_client().get_object(
        Bucket=photo["s3_bucket"],
        Key=photo["s3_key"]
    )["Body"].read()

    thumbnail = create_guest_thumbnail(
        image_bytes,
        photo["contributor_name"]
    )

    return Response(
        content=thumbnail,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"
        }
    )


@app.get("/api/guest/photos")
async def list_guest_photos(request: Request):
    """
    Return photographs authorized for this guest session.
    Development-only access.
    """
    from fastapi.responses import JSONResponse
    from guest_sessions import list_guest_authorized_photos

    guest_token = request.cookies.get("tam_guest_photos")

    if not guest_token:
        return JSONResponse(
            {"error": "Guest photo session is missing or expired."},
            status_code=401
        )

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        photos = list_guest_authorized_photos(
            connection,
            guest_token,
            EVENT_ID
        )

    return JSONResponse(
        {
            "status": "success",
            "count": len(photos),
            "photos": photos
        },
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff"
        }
    )

@app.get("/find-my-photos", response_class=HTMLResponse)
async def find_my_photos(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="find_my_photos.html",
        context={}
    )

@app.post("/api/find-my-photos/search")
async def search_guest_photos(request: Request):
    """
    Local development-only selfie search endpoint.
    Does not save or index the submitted selfie.
    """
    from fastapi.responses import JSONResponse
    from rekognition_service import find_matching_event_photos

    
    # Verify the temporary consent-flow cookie before
    # accepting a selfie or calling AWS Rekognition.
    from guest_sessions import (
        CONSENT_FLOW_COOKIE_NAME,
        validate_guest_consent_flow,
        consume_guest_consent_flow
    )

    flow_token = request.cookies.get(
        CONSENT_FLOW_COOKIE_NAME
    )

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        consent_id = validate_guest_consent_flow(
            connection,
            flow_token,
            EVENT_ID
        )

    if consent_id is None:
        return JSONResponse(
            {
                "error": (
                    "Your consent session is missing or expired. "
                    "Please complete the consent form again."
                )
            },
            status_code=403
        )




    # Allow selfie searches only from the official HTTPS website.
    allowed_origin = "https://tam-smartphotos.duckdns.org"
    origin = request.headers.get("origin", "")

    if origin != allowed_origin:
        return JSONResponse(
            {"error": "Invalid photo-search request origin."},
            status_code=403
        )

    # Require HTTPS at the public reverse proxy.
    forwarded_proto = request.headers.get(
        "x-forwarded-proto", ""
    )

    if forwarded_proto != "https":
        return JSONResponse(
            {"error": "Secure HTTPS connection required."},
            status_code=403
        )

    if request.headers.get("content-length"):
        if int(request.headers["content-length"]) > 6 * 1024 * 1024:
            return JSONResponse(
                {"error": "Selfie is too large."},
                status_code=413
            )

    form = await request.form()

    if form.get("consent") != "yes":
        return JSONResponse(
            {"error": "Facial-search consent is required."},
            status_code=400
        )

    selfie = form.get("selfie")

    if selfie is None or not hasattr(selfie, "read"):
        return JSONResponse(
            {"error": "Please capture a selfie first."},
            status_code=400
        )

    selfie_bytes = await selfie.read(5 * 1024 * 1024 + 1)

    if len(selfie_bytes) > 5 * 1024 * 1024:
        return JSONResponse(
            {"error": "Selfie exceeds the 5 MB limit."},
            status_code=413
        )

    
    # Revoke any previous photo session in this browser
    # before starting a new facial-recognition search.
    from guest_sessions import revoke_previous_guest_photo_session

    previous_guest_token = request.cookies.get("tam_guest_photos")

    if previous_guest_token:
        with sqlite3.connect(DB_PATH, timeout=10) as connection:
            revoke_previous_guest_photo_session(
                connection,
                previous_guest_token
            )


    # Atomically consume the consent-flow token.
    # Only one selfie search may use this token.
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        consumed_consent_id = consume_guest_consent_flow(
            connection,
            flow_token,
            EVENT_ID
        )

    if consumed_consent_id is None:
        return JSONResponse(
            {
                "error": (
                    "Your consent session has expired or "
                    "has already been used. "
                    "Please complete the consent form again."
                )
            },
            status_code=403
        )

    consent_id = consumed_consent_id


    try:
        matches = find_matching_event_photos(selfie_bytes)

    except ValueError as error:
        response = JSONResponse(
            {"error": str(error)},
            status_code=400
        )

        response.delete_cookie(
            key="tam_guest_photos",
            path="/",
            samesite="lax"
        )
        # Remove the consumed consent-flow cookie.
        response.delete_cookie(
            key=CONSENT_FLOW_COOKIE_NAME,
            path="/",
            samesite="lax"
        )
        # Clear the consumed consent-flow cookie.
        response.delete_cookie(
            key=CONSENT_FLOW_COOKIE_NAME,
            path="/",
            samesite="lax"
        )

        return response

    except Exception:
        response = JSONResponse(
            {"error": "Photo search is temporarily unavailable."},
            status_code=503
        )

        response.delete_cookie(
            key="tam_guest_photos",
            path="/",
            samesite="lax"
        )

        return response

    # Extract photo IDs from server-side Rekognition matches.
    authorized_photo_ids = sorted({
        match["photo_id"]
        for match in matches
        if type(match.get("photo_id")) is int
        and match["photo_id"] > 0
    })

 

    if not authorized_photo_ids:
        response = JSONResponse({
            "status": "success",
            "match_count": 0,
            "matches": [],
            "message": (
                "No matching photographs found yet. "
                "Please check again later."
            )
        })

        # Remove any previous guest photo cookie.
        response.delete_cookie(
            key="tam_guest_photos",
            path="/",
            samesite="lax"
        )

        # Clear the consumed, single-use consent cookie.
        response.delete_cookie(
            key=CONSENT_FLOW_COOKIE_NAME,
            path="/",
            samesite="lax"
        )

        return response
    
    # Create a restricted guest photo session.
    from guest_sessions import create_guest_session

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        guest_token = create_guest_session(
            connection,
            EVENT_ID,
            consent_id,
            authorized_photo_ids
        )

    response = JSONResponse({
        "status": "success",
        "match_count": len(authorized_photo_ids),
        "message": "Your matching photographs are ready."
    })

    # Store access token in an HttpOnly cookie.
    # HTTPS must be enabled before public deployment.
    response.set_cookie(
        key="tam_guest_photos",
        value=guest_token,
        max_age=30 * 60,
        httponly=True,
        secure=True,  # HTTPS-only guest session cookie
        samesite="lax",
        path="/"
    )
    # Clear the consumed consent-flow cookie.
    response.delete_cookie(
        key=CONSENT_FLOW_COOKIE_NAME,
        path="/",
        samesite="lax"
    )

    return response


   





EVENT_PORTAL_URL = "/events/tam-diwali-2026"
EVENT_CONSENT_URL = "/attendee-consent?entry=event"
EVENT_CONSENT_COOKIE = "tam_event_consent"
EVENT_CONSENT_MAX_AGE = 24 * 60 * 60


def event_consent_signer():
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(
        SESSION_SECRET, salt="tam-event-consent-v1"
    )


def active_event_selfie_consent(request):
    from itsdangerous import BadData

    token = request.cookies.get(EVENT_CONSENT_COOKIE)
    if not token:
        return None

    try:
        claim = event_consent_signer().loads(token, max_age=EVENT_CONSENT_MAX_AGE)
    except BadData:
        return None

    if claim["event_id"] != EVENT_ID:
        return None

    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        row = connection.execute(
            """
            SELECT search.id
            FROM attendee_consents AS search
            JOIN attendee_consents AS indexing
              ON indexing.event_id = search.event_id
            WHERE search.id = ? AND indexing.id = ?
              AND search.event_id = ?
              AND search.consent_type = 'selfie_search'
              AND indexing.consent_type = 'facial_indexing'
              AND search.accepted_at IS NOT NULL
              AND indexing.accepted_at IS NOT NULL
              AND search.withdrawn_at IS NULL
              AND indexing.withdrawn_at IS NULL
            """,
            (claim["selfie_id"], claim["indexing_id"], EVENT_ID),
        ).fetchone()

    return row[0] if row else None


@app.middleware("http")
async def require_event_photo_consent(request: Request, call_next):
    from fastapi.responses import JSONResponse

    path = request.url.path
    photo_page = path in {
        "/contributor/camera", "/contributor/upload", "/contributor/register",
        "/find-my-photos", "/gallery", "/gallery/register",
    }
    photo_api = path in {"/upload", "/register"} or path.startswith((
        "/api/gallery/", "/api/find-my-photos/", "/api/guest/photos/",
    ))

    if photo_page or photo_api:
        request.state.event_selfie_consent_id = active_event_selfie_consent(request)

    if (photo_page or photo_api) and request.state.event_selfie_consent_id is None:
        headers = {"Cache-Control": "no-store"}
        if photo_page and request.method == "GET":
            return RedirectResponse(EVENT_CONSENT_URL, status_code=303, headers=headers)
        return JSONResponse(
            {"detail": "Open the event page and complete the photo consent form first."},
            status_code=403,
            headers=headers,
        )

    return await call_next(request)


@app.get(
    "/events/tam-diwali-2026",
    response_class=HTMLResponse
)
async def tam_event_portal(request: Request):
    agenda_only = request.query_params.get("agenda") == "1"
    headers = {"Cache-Control": "no-store"}

    if not agenda_only and active_event_selfie_consent(request) is None:
        return RedirectResponse(EVENT_CONSENT_URL, status_code=303, headers=headers)

    return templates.TemplateResponse(
        request=request,
        name="event_portal.html",
        context={"agenda_only": agenda_only},
        headers=headers,
    )



@app.get("/attendee-consent", response_class=HTMLResponse)
async def attendee_consent_page(request: Request):
    from consent_service import (
        CONSENT_TEXTS,
        CONSENT_FORM_STATUS
    )

    return templates.TemplateResponse(
        request=request,
        name="attendee_consent.html",
        context={
            "consent_texts": CONSENT_TEXTS,
            "form_status": CONSENT_FORM_STATUS
        }
    )


@app.post("/api/attendee-consent")
async def submit_attendee_consent(request: Request):
    """
    Development-only consent recording.
    Do not expose this endpoint publicly yet.
    """
    from fastapi.responses import JSONResponse
    from consent_service import (
        CONSENT_FORM_STATUS,
        record_attendee_consent
    )
    from guest_sessions import (
        create_guest_consent_flow,
        revoke_guest_consent_flow,
        CONSENT_FLOW_COOKIE_NAME,
        CONSENT_FLOW_DURATION_SECONDS
    )

    if CONSENT_FORM_STATUS != "development":
        return JSONResponse(
            {"error": "Consent submission is not configured."},
            status_code=503
        )

    # Only accept submissions from the official TAM HTTPS website.
    origin = request.headers.get("origin", "")

    if origin != "https://tam-smartphotos.duckdns.org":
        return JSONResponse(
            {"error": "Invalid request origin."},
            status_code=403
        )

    if request.headers.get("content-length", "").isdigit():
        if int(request.headers["content-length"]) > 4096:
            return JSONResponse(
                {"error": "Request too large."},
                status_code=413
            )

    form = await request.form()

    # Revoke any previous guest photo session in this browser.
    # This prevents another attendee using the same phone
    # from inheriting earlier photo access.
    from guest_sessions import revoke_guest_session

    # Revoke an older consent flow before starting a new one.
    previous_consent_token = request.cookies.get(
        CONSENT_FLOW_COOKIE_NAME
    )

    if previous_consent_token:
        with sqlite3.connect(DB_PATH, timeout=10) as connection:
            revoke_guest_consent_flow(
                connection,
                previous_consent_token
            )

    previous_photo_token = request.cookies.get(
        "tam_guest_photos"
    )

    if previous_photo_token:
        with sqlite3.connect(DB_PATH, timeout=10) as connection:
            revoke_guest_session(
                connection,
                previous_photo_token
            )


    name = str(form.get("attendee_name", "")).strip()
    mobile = str(form.get("attendee_mobile", "")).strip()

   
    if not mobile:
        return JSONResponse(
            {"error": "Please enter your mobile number."},
            status_code=400
        )

    # Combined consent decision from the TAM form.
    decision = str(form.get("photo_consent", "")).strip()

    if decision not in ("agree", "decline"):
        return JSONResponse(
            {"error": "Please select I Agree or I Decline."},
            status_code=400
        )

    
    if decision == "decline":
        response = JSONResponse({
            "status": "declined",
            "message": (
                "Your decision has been respected. "
                "Facial recognition will not be performed."
            )
        })

        response.delete_cookie(
            key=CONSENT_FLOW_COOKIE_NAME,
            path="/",
            samesite="lax"
        )

        response.delete_cookie(
            key="tam_guest_photos",
            path="/",
            samesite="lax"
        )

        response.delete_cookie(EVENT_CONSENT_COOKIE, path="/", secure=True)
        response.delete_cookie("tam_gallery_session", path="/", secure=True)
        response.delete_cookie("tam_gallery_session", path="/api/gallery", secure=True)
        return response


    # Combined agreement authorizes both disclosed activities.
    indexing = True
    searching = True

   


    if not name or len(name) > 150:
        return JSONResponse(
            {"error": "Enter a valid full name."},
            status_code=400
        )

    records = []

    try:
        if indexing:
            records.append(
                record_attendee_consent(
                    name, "facial_indexing", mobile
                )
            )

        if searching:
            records.append(
                record_attendee_consent(
                    name, "selfie_search", mobile
                )
            )

    except ValueError as error:
        return JSONResponse(
            {"error": str(error)},
            status_code=400
        )

    
    # Find the selfie-search consent created by this submission.
    selfie_consent_id = next(
        (
            record["consent_id"]
            for record in records
            if record["consent_type"] == "selfie_search"
        ),
        None
    )

    if selfie_consent_id is None:
        return JSONResponse(
            {"error": "Selfie-search consent was not recorded."},
            status_code=500
        )

    # Create a server-side, 15-minute consent flow.    
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        flow_token = create_guest_consent_flow(
            connection,
            selfie_consent_id,
            EVENT_ID
        )

    response = JSONResponse({
        "status": "recorded",
        "consents": records
    })

    indexing_consent_id = next(
        record["consent_id"] for record in records
        if record["consent_type"] == "facial_indexing"
    )
    response.set_cookie(
        EVENT_CONSENT_COOKIE,
        event_consent_signer().dumps({
            "event_id": EVENT_ID,
            "selfie_id": selfie_consent_id,
            "indexing_id": indexing_consent_id,
        }),
        max_age=EVENT_CONSENT_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )

    response.set_cookie(
        key=CONSENT_FLOW_COOKIE_NAME,
        value=flow_token,
        max_age=CONSENT_FLOW_DURATION_SECONDS,
        httponly=True,
        secure=True,  # HTTPS-only guest consent cookie.
        samesite="lax",
        path="/"
    )

    # Clear any previous attendee's photo-access cookie.
    response.delete_cookie(
        key="tam_guest_photos",
        path="/",
        samesite="lax"
    )

    response.delete_cookie("tam_gallery_session", path="/", secure=True)
    response.delete_cookie("tam_gallery_session", path="/api/gallery", secure=True)
    return response



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
        "event_id": "tam-diwali-2026",
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
              AND p.event_id = 'tam-diwali-2026'
              AND p.status = 'available'
            """,
            (photo_id,)
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
