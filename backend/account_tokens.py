"""Signed, expiring, single-use tokens for emailed links.

The signature (itsdangerous, keyed with SESSION_SECRET and a per-purpose
salt) stops forgery; the account_tokens row (hash only) makes each token
usable once and lets it expire even if the signature is still valid.
"""

import hashlib
import secrets
import sqlite3
import time

from itsdangerous import BadSignature, URLSafeSerializer

from admin_auth import SESSION_SECRET
from database import DB_PATH

PURPOSES = ("approve", "reset")
KINDS = ("contributor", "attendee")


def _serializer(purpose: str) -> URLSafeSerializer:
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown token purpose: {purpose!r}")
    return URLSafeSerializer(SESSION_SECRET, salt=f"tam-account-{purpose}")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(purpose: str, kind: str, account_id: int, ttl_seconds: int) -> str:
    if kind not in KINDS:
        raise ValueError(f"Unknown account kind: {kind!r}")
    token = _serializer(purpose).dumps(
        {"n": secrets.token_urlsafe(16), "k": kind, "a": account_id})
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        connection.execute(
            "INSERT INTO account_tokens (token_hash, purpose, account_kind, "
            "account_id, expires_at) VALUES (?, ?, ?, ?, ?)",
            (_hash(token), purpose, kind, account_id, int(time.time()) + ttl_seconds)
        )
    return token


def _decode(token: str, purpose: str) -> tuple[str, int] | None:
    try:
        data = _serializer(purpose).loads(token)
    except BadSignature:
        return None
    if data.get("k") not in KINDS or not isinstance(data.get("a"), int):
        return None
    return data["k"], data["a"]


def peek(token: str, purpose: str) -> tuple[str, int] | None:
    """(kind, account_id) if the token is valid, unused and unexpired."""
    decoded = _decode(token, purpose)
    if decoded is None:
        return None
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        row = connection.execute(
            "SELECT 1 FROM account_tokens WHERE token_hash = ? AND purpose = ? "
            "AND account_kind = ? AND account_id = ? AND used_at IS NULL "
            "AND expires_at > ?",
            (_hash(token), purpose, *decoded, int(time.time()))
        ).fetchone()
    return decoded if row else None


def consume(token: str, purpose: str) -> tuple[str, int] | None:
    """Atomically mark the token used. Returns (kind, id) only the first time."""
    decoded = _decode(token, purpose)
    if decoded is None:
        return None
    now = int(time.time())
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        cursor = connection.execute(
            "UPDATE account_tokens SET used_at = ? WHERE token_hash = ? "
            "AND purpose = ? AND account_kind = ? AND account_id = ? "
            "AND used_at IS NULL AND expires_at > ?",
            (now, _hash(token), purpose, *decoded, now)
        )
    return decoded if cursor.rowcount == 1 else None
