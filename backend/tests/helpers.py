"""Small database helpers shared by tests."""

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher

from database import DB_PATH
from settings import SETTINGS

HASHER = PasswordHasher()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_event(auto_authorize: bool = False, gallery: bool = False) -> None:
    today = datetime.now(ZoneInfo("America/Toronto")).date().isoformat()
    with db() as conn:
        conn.execute(
            "INSERT INTO events (event_id, event_name, event_date, "
            "upload_start_time, upload_end_time, status, "
            "auto_authorize_face_indexing, gallery_enabled) "
            "VALUES (?, 'Test Event', ?, '00:00', '23:59', 'active', ?, ?)",
            (SETTINGS.event_id, today, int(auto_authorize), int(gallery)),
        )


def create_admin(username="boss", password="super-secret-pass",
                 role="super_admin", must_change=False, active=True,
                 can_approve=False, can_manage_photos=False) -> int:
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO admins (name, username, password_hash, role, active, "
            "must_change_password) VALUES (?, ?, ?, ?, ?, ?)",
            (username.title(), username, HASHER.hash(password), role,
             int(active), int(must_change)),
        )
        if can_approve or can_manage_photos:
            conn.execute(
                "INSERT INTO organizer_permissions VALUES (?, ?, ?)",
                (cursor.lastrowid, int(can_approve), int(can_manage_photos)),
            )
        return cursor.lastrowid


def create_contributor(name="Asha Rao", email="asha@example.com",
                       username=None, password="contrib-pass-1",
                       approved=True) -> int:
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO contributors (name, event_id, approved, email, "
            "username, password_hash, mobile_number) "
            "VALUES (?, ?, ?, ?, ?, ?, '5145550100')",
            (name, SETTINGS.event_id, int(approved), email, username,
             HASHER.hash(password)),
        )
        return cursor.lastrowid


def create_attendee(full_name="Ravi Kumar", email="ravi@example.com",
                    username=None, password="attendee-pass-1",
                    approved=True) -> int:
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO attendees (event_id, full_name, email, username, "
            "password_hash, approved) VALUES (?, ?, ?, ?, ?, ?)",
            (SETTINGS.event_id, full_name, email, username,
             HASHER.hash(password), int(approved)),
        )
        return cursor.lastrowid


def create_photo(contributor_id: int, s3_key: str, authorized: bool = True) -> int:
    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO photos (event_id, contributor_id, filename, file_path, "
            "status, s3_bucket, s3_key) VALUES (?, ?, 'p.jpg', '/nowhere/p.jpg', "
            "'available', 'tam-smart-event-photos', ?)",
            (SETTINGS.event_id, contributor_id, s3_key),
        )
        photo_id = cursor.lastrowid
        if authorized:
            conn.execute(
                "INSERT INTO photo_face_authorizations VALUES "
                "(?, 1, 'test-policy', CURRENT_TIMESTAMP)",
                (photo_id,),
            )
        return photo_id
