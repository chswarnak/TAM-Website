"""Failed-login throttling backed by SQLite.

Two limits per scope (e.g. "admin", "contributor", "attendee"):
  * per (IP, identifier): stops password guessing against one account;
  * per IP: stops spraying many accounts. Its default is high because
    everyone at the venue shares one public IP.
Only failures count, and a successful login clears that identifier's
failures. Keys are stored as HMACs so no raw IPs or emails are kept.
"""

import hashlib
import hmac
import sqlite3
import time

from admin_auth import SESSION_SECRET
from database import DB_PATH
from settings import SETTINGS


def _key(*parts: str) -> str:
    message = "|".join(parts).encode("utf-8")
    return hmac.new(SESSION_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _keys(scope: str, ip: str, identifier: str) -> tuple[str, str]:
    identifier = (identifier or "").strip().lower()
    return _key(scope, "id", ip, identifier), _key(scope, "ip", ip)


def _window() -> int:
    return SETTINGS.login_window_minutes * 60


def retry_after(scope: str, ip: str, identifier: str) -> int | None:
    """Seconds until another attempt is allowed, or None if allowed now."""
    identity_key, ip_key = _keys(scope, ip, identifier)
    now = time.time()
    cutoff = now - _window()
    limits = (
        (identity_key, SETTINGS.login_max_failures_per_identifier),
        (ip_key, SETTINGS.login_max_failures_per_ip),
    )
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        for key, limit in limits:
            count, oldest = connection.execute(
                "SELECT COUNT(*), MIN(attempted_at) FROM login_attempts "
                "WHERE scope = ? AND key_hash = ? AND attempted_at > ?",
                (scope, key, cutoff)
            ).fetchone()
            if count >= limit:
                return max(1, int(oldest + _window() - now))
    return None


def record_failure(scope: str, ip: str, identifier: str) -> None:
    identity_key, ip_key = _keys(scope, ip, identifier)
    now = time.time()
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute(
            "DELETE FROM login_attempts WHERE attempted_at <= ?",
            (now - _window(),)
        )
        connection.executemany(
            "INSERT INTO login_attempts (scope, key_hash, attempted_at) "
            "VALUES (?, ?, ?)",
            [(scope, identity_key, now), (scope, ip_key, now)]
        )


def clear_identifier(scope: str, ip: str, identifier: str) -> None:
    identity_key, _ = _keys(scope, ip, identifier)
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute(
            "DELETE FROM login_attempts WHERE scope = ? AND key_hash = ?",
            (scope, identity_key)
        )
