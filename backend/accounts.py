"""Contributor and attendee accounts: registration, login, approval.

Both account kinds follow the same identity rules:
  * the full name is display-only; it is never used for login or file paths;
  * login is by email (required, unique) or an optional username (unique);
  * nobody can log in until an admin approves the account.
"""

import re
import sqlite3
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request

from database import DB_PATH
from settings import SETTINGS

KINDS = ("contributor", "attendee")
# Whitelist: table and column names are never taken from user input.
ACCOUNT_TABLES = {"contributor": "contributors", "attendee": "attendees"}
NAME_COLUMNS = {"contributor": "name", "attendee": "full_name"}
SESSION_KEYS = {"contributor": "contributor_id", "attendee": "attendee_id"}

MIN_PASSWORD_LENGTH = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,30}$")
MOBILE_RE = re.compile(r"^\+?[0-9 ().-]{7,20}$")

password_hasher = PasswordHasher()
# Verified when no account matches, so response time does not reveal
# whether an identifier exists.
_DUMMY_HASH = password_hasher.hash("tam-dummy-password-for-timing")


class AccountError(ValueError):
    """Invalid account data; the message is safe to show to users."""
    status_code = 400


class AccountConflict(AccountError):
    status_code = 409


class LoginError(Exception):
    status_code = 401
    message = "Invalid email/username or password."


class AccountPending(LoginError):
    status_code = 403
    message = "Your account is awaiting approval by the TAM administrator."


class AccountDisabled(LoginError):
    status_code = 403
    message = "This account has been disabled."


@dataclass(frozen=True)
class Identity:
    email: str
    username: str | None


def table_for(kind: str) -> str:
    try:
        return ACCOUNT_TABLES[kind]
    except KeyError as error:
        raise AccountError(f"Unknown account type: {kind!r}") from error


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


# ---------------------------------------------------------------- validation

def normalize_identity(email: str | None, username: str | None) -> Identity:
    email = (email or "").strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AccountError("Please enter a valid email address.")

    username = (username or "").strip().lower() or None
    if username is not None and not USERNAME_RE.match(username):
        raise AccountError(
            "Username must be 3-30 characters: letters, numbers, "
            "dots, dashes or underscores (no spaces)."
        )
    return Identity(email=email, username=username)


def _normalize_name(full_name: str | None) -> str:
    name = " ".join((full_name or "").split())
    if not 1 <= len(name) <= 100:
        raise AccountError("Please enter your full name (up to 100 characters).")
    return name


def _normalize_mobile(mobile: str | None, required: bool) -> str | None:
    mobile = (mobile or "").strip()
    if not mobile:
        if required:
            raise AccountError("Mobile number is required.")
        return None
    if not MOBILE_RE.match(mobile):
        raise AccountError("Please enter a valid mobile number.")
    return mobile


def validate_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AccountError(
            f"Password must contain at least {MIN_PASSWORD_LENGTH} characters."
        )


def _conflict_from(error: sqlite3.IntegrityError) -> AccountConflict:
    field = "username" if "username" in str(error) else "email"
    return AccountConflict(f"That {field} is already registered.")


# ------------------------------------------------------------- registration

def register(kind: str, full_name: str, email: str, username: str | None,
             mobile_number: str | None, password: str) -> int:
    """Create a pending (unapproved) account and return its id."""
    table = table_for(kind)
    name = _normalize_name(full_name)
    identity = normalize_identity(email, username)
    mobile = _normalize_mobile(mobile_number, required=(kind == "contributor"))
    validate_password(password)
    password_hash = password_hasher.hash(password)

    columns = {
        "contributor": "(name, event_id, email, username, mobile_number, "
                       "password_hash, approved, registered_at)",
        "attendee": "(full_name, event_id, email, username, mobile_number, "
                    "password_hash, approved, registered_at)",
    }[kind]
    try:
        with _connect() as connection:
            cursor = connection.execute(
                f"INSERT INTO {table} {columns} "
                "VALUES (?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)",
                (name, SETTINGS.event_id, identity.email, identity.username,
                 mobile, password_hash)
            )
            return cursor.lastrowid
    except sqlite3.IntegrityError as error:
        raise _conflict_from(error) from error


def set_login(kind: str, account_id: int, email: str, username: str | None) -> None:
    """Admin action: give an existing account its login identifiers."""
    table = table_for(kind)
    identity = normalize_identity(email, username)
    try:
        with _connect() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET email = ?, username = ? "
                "WHERE id = ? AND event_id = ? AND deleted_at IS NULL",
                (identity.email, identity.username, account_id, SETTINGS.event_id)
            )
    except sqlite3.IntegrityError as error:
        raise _conflict_from(error) from error
    if cursor.rowcount == 0:
        raise AccountError("Account not found.")


# -------------------------------------------------------------------- login

def authenticate(kind: str, identifier: str, password: str) -> dict:
    """Return the account row, or raise a LoginError subclass."""
    table = table_for(kind)
    identifier = (identifier or "").strip().lower()
    column = "email" if "@" in identifier else "username"

    row = None
    if identifier:
        with _connect() as connection:
            row = connection.execute(
                f"""
                SELECT id, password_hash, approved, active, deleted_at,
                       must_change_password
                FROM {table}
                WHERE lower({column}) = ? AND event_id = ?
                """,
                (identifier, SETTINGS.event_id)
            ).fetchone()

    stored_hash = row["password_hash"] if row and row["password_hash"] else _DUMMY_HASH
    try:
        password_hasher.verify(stored_hash, password or "")
    except (VerifyMismatchError, VerificationError, InvalidHashError) as error:
        raise LoginError() from error
    if row is None:
        raise LoginError()
    if not row["active"] or row["deleted_at"]:
        raise AccountDisabled()
    if not row["approved"]:
        raise AccountPending()
    return dict(row)


def change_password(kind: str, account_id: int, new_password: str,
                    must_change: bool = False) -> None:
    """Set a new password. must_change=True makes it a temporary password."""
    table = table_for(kind)
    validate_password(new_password)
    with _connect() as connection:
        cursor = connection.execute(
            f"UPDATE {table} SET password_hash = ?, must_change_password = ? "
            "WHERE id = ? AND event_id = ? AND deleted_at IS NULL",
            (password_hasher.hash(new_password), int(must_change), account_id,
             SETTINGS.event_id)
        )
    if cursor.rowcount == 0:
        raise AccountError("Account not found.")


def find_active_by_email(kind: str, email: str) -> int | None:
    """Id of the approved, active account with this email, if any."""
    table = table_for(kind)
    with _connect() as connection:
        row = connection.execute(
            f"SELECT id FROM {table} WHERE lower(email) = ? AND event_id = ? "
            "AND approved = 1 AND active = 1 AND deleted_at IS NULL",
            ((email or "").strip().lower(), SETTINGS.event_id)
        ).fetchone()
    return row["id"] if row else None


def logged_in_kind(request: Request) -> str | None:
    for kind, key in SESSION_KEYS.items():
        if isinstance(request.session.get(key), int):
            return kind
    return None


def verify_password(kind: str, account_id: int, password: str) -> bool:
    table = table_for(kind)
    with _connect() as connection:
        row = connection.execute(
            f"SELECT password_hash FROM {table} WHERE id = ?", (account_id,)
        ).fetchone()
    try:
        return bool(row) and password_hasher.verify(row["password_hash"], password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ------------------------------------------------------------------ session

def start_session(request: Request, kind: str, account_id: int) -> None:
    request.session.clear()
    request.session[SESSION_KEYS[kind]] = account_id


def session_account(request: Request, kind: str) -> dict | None:
    """The logged-in, approved, active account of this kind, or None.

    Re-reads the database on every call, so disabling or deleting an
    account takes effect immediately.
    """
    account_id = request.session.get(SESSION_KEYS[kind])
    if not isinstance(account_id, int):
        return None
    table = table_for(kind)
    with _connect() as connection:
        row = connection.execute(
            f"""
            SELECT *, {NAME_COLUMNS[kind]} AS display_name
            FROM {table}
            WHERE id = ? AND event_id = ? AND approved = 1
              AND active = 1 AND deleted_at IS NULL
            """,
            (account_id, SETTINGS.event_id)
        ).fetchone()
    return dict(row) if row else None


def require_account(request: Request, kind: str) -> dict:
    """For APIs: the session account, or 401; 403 while a password change
    is pending."""
    account = session_account(request, kind)
    if account is None:
        raise HTTPException(status_code=401, detail="Please log in first.")
    if account["must_change_password"]:
        raise HTTPException(status_code=403,
                            detail="Please change your password first.")
    return account


# ----------------------------------------------------------------- approval

def approve(kind: str, account_ids: list[int], admin_id: int) -> int:
    """Approve pending, active accounts. Returns how many were approved."""
    table = table_for(kind)
    if not account_ids:
        return 0
    placeholders = ",".join("?" for _ in account_ids)
    with _connect() as connection:
        cursor = connection.execute(
            f"""
            UPDATE {table}
            SET approved = 1, approved_by_admin_id = ?,
                approved_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
              AND event_id = ? AND approved = 0
              AND active = 1 AND deleted_at IS NULL
            """,
            (admin_id, *account_ids, SETTINGS.event_id)
        )
        return cursor.rowcount


def set_active(kind: str, account_id: int, active: bool) -> bool:
    """Disable or re-enable a (not deleted) account. True if it changed."""
    table = table_for(kind)
    with _connect() as connection:
        cursor = connection.execute(
            f"UPDATE {table} SET active = ? "
            "WHERE id = ? AND event_id = ? AND deleted_at IS NULL",
            (int(active), account_id, SETTINGS.event_id)
        )
    return cursor.rowcount == 1


def soft_delete(kind: str, account_id: int) -> bool:
    """Delete an account (kept for audit history). True if it changed."""
    table = table_for(kind)
    with _connect() as connection:
        cursor = connection.execute(
            f"UPDATE {table} SET active = 0, deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND event_id = ? AND deleted_at IS NULL",
            (account_id, SETTINGS.event_id)
        )
    return cursor.rowcount == 1


def list_accounts(kind: str, pending_only: bool = False,
                  include_disabled: bool = False) -> list[dict]:
    """Non-deleted accounts; disabled ones only when include_disabled."""
    table = table_for(kind)
    pending_clause = "AND approved = 0" if pending_only else ""
    active_clause = "" if include_disabled else "AND active = 1"
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT id, {NAME_COLUMNS[kind]} AS full_name, email, username,
                   mobile_number, approved, active
            FROM {table}
            WHERE event_id = ? AND deleted_at IS NULL
              {active_clause}
              {pending_clause}
            ORDER BY id
            """,
            (SETTINGS.event_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def mask_mobile(mobile: str | None) -> str | None:
    digits = "".join(c for c in (mobile or "") if c.isdigit())
    return "****" + digits[-4:] if len(digits) >= 4 else None
