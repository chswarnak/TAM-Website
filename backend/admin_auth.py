"""Administrator authentication, permissions and CSRF tokens.

Kept outside main.py so feature routers can share one implementation of
"is this request from an admin who may do X?".
"""

import os
import sqlite3

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from database import DB_PATH

SESSION_SECRET = os.environ["SESSION_SECRET"]
PERMISSIONS = frozenset({"can_approve_contributors", "can_manage_photos"})

password_hasher = PasswordHasher()
csrf_serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="tam-admin-csrf")


def create_csrf_token(admin_id: int, csrf_nonce: str) -> str:
    """Create a signed CSRF token for the current login session."""
    return csrf_serializer.dumps({"admin_id": admin_id, "csrf_nonce": csrf_nonce})


def verify_csrf_token(token: str, admin_id: int, csrf_nonce: str) -> bool:
    """Verify the token belongs to the current login session."""
    if not csrf_nonce:
        return False
    try:
        data = csrf_serializer.loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        return False
    return data.get("admin_id") == admin_id and data.get("csrf_nonce") == csrf_nonce


def authenticate_admin(username: str, password: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        admin = connection.execute(
            """
            SELECT id, name, username, password_hash, role, must_change_password
            FROM admins
            WHERE username = ? AND active = 1 AND deleted_at IS NULL
            """,
            (username.strip().lower(),)
        ).fetchone()

    if admin is None:
        return None
    try:
        password_hasher.verify(admin["password_hash"], password)
    except (VerifyMismatchError, VerificationError):
        return None

    return {
        "id": admin["id"],
        "name": admin["name"],
        "username": admin["username"],
        "role": admin["role"],
        "must_change_password": bool(admin["must_change_password"]),
    }


def has_admin_permission(admin_id: int, permission: str) -> bool:
    """True only for an active, non-deleted admin who has finished password
    setup and is a super admin or holds the organizer permission."""
    if permission not in PERMISSIONS:
        return False

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        admin = connection.execute(
            """
            SELECT role, must_change_password
            FROM admins
            WHERE id = ? AND active = 1 AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        if admin is None or admin["must_change_password"]:
            return False
        if admin["role"] == "super_admin":
            return True
        if admin["role"] != "admin":
            return False

        permissions = connection.execute(
            "SELECT can_approve_contributors, can_manage_photos "
            "FROM organizer_permissions WHERE admin_id = ?",
            (admin_id,)
        ).fetchone()

    return bool(permissions and permissions[permission])


def require_admin_permission(
    request: Request,
    permission: str,
    csrf_token: str | None = None,
) -> int:
    """Return the admin id, or raise 401/403.

    Pass csrf_token for state-changing requests; it is then verified
    against the current login session.
    """
    admin_id = request.session.get("admin_id")
    if not isinstance(admin_id, int):
        raise HTTPException(status_code=401, detail="Administrator login required")

    if csrf_token is not None and not verify_csrf_token(
        csrf_token, admin_id, request.session.get("csrf_nonce", "")
    ):
        raise HTTPException(status_code=403, detail="Invalid or expired security token")

    if not has_admin_permission(admin_id, permission):
        raise HTTPException(status_code=403, detail="Permission denied")

    return admin_id


def require_super_admin(request: Request, csrf_token: str | None = None) -> int:
    """Return the id of an active Super Admin who has finished password
    setup, or raise 401/403. Verifies csrf_token when given."""
    admin_id = request.session.get("admin_id")
    if not isinstance(admin_id, int):
        raise HTTPException(status_code=401, detail="Administrator login required")
    if csrf_token is not None and not verify_csrf_token(
        csrf_token, admin_id, request.session.get("csrf_nonce", "")
    ):
        raise HTTPException(status_code=403, detail="Invalid or expired security token")
    with sqlite3.connect(DB_PATH) as connection:
        admin = connection.execute(
            "SELECT role, must_change_password FROM admins "
            "WHERE id = ? AND active = 1 AND deleted_at IS NULL",
            (admin_id,)
        ).fetchone()
    if admin is None or admin[0] != "super_admin" or admin[1]:
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return admin_id
