"""Email delivery (AWS SES or SMTP) and the new-registration digest.

Provider is chosen by TAM_EMAIL_PROVIDER ("ses" or "smtp"); when unset,
no email is sent and registrations simply wait on the admin dashboard.

The digest batches registrations: at most one email per
TAM_NOTIFY_INTERVAL_MINUTES, listing every not-yet-notified pending
account with a single-use Approve link.
"""

import asyncio
import logging
import smtplib
import sqlite3
import time
from email.message import EmailMessage

from botocore.exceptions import BotoCoreError, ClientError

import account_tokens
from aws_config import get_tam_aws_session
from database import DB_PATH
from settings import SETTINGS

logger = logging.getLogger(__name__)

SES_REGION = "ca-central-1"
APPROVE_LINK_TTL_SECONDS = 72 * 3600
LOOP_SECONDS = 60
DIGEST_STATE = "registration_digest"
LABELS = {"contributor": "Contributor", "attendee": "Attendee"}
_PENDING_SQL = {
    "contributor": "SELECT id, name AS full_name, email FROM contributors",
    "attendee": "SELECT id, full_name, email FROM attendees",
}


class EmailError(RuntimeError):
    """Sending an email failed."""


def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email with the configured provider."""
    if SETTINGS.email_provider == "ses":
        _send_ses(to, subject, body)
    elif SETTINGS.email_provider == "smtp":
        _send_smtp(to, subject, body)
    else:
        raise EmailError("No email provider configured (TAM_EMAIL_PROVIDER).")


def _send_ses(to: str, subject: str, body: str) -> None:
    try:
        client = get_tam_aws_session().client("ses", region_name=SES_REGION)
        client.send_email(
            Source=SETTINGS.email_from,
            Destination={"ToAddresses": [to]},
            Message={"Subject": {"Data": subject, "Charset": "UTF-8"},
                     "Body": {"Text": {"Data": body, "Charset": "UTF-8"}}},
        )
    except (ClientError, BotoCoreError, RuntimeError) as error:
        raise EmailError(f"SES send failed: {error}") from error


def _send_smtp(to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = SETTINGS.email_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(SETTINGS.smtp_host, SETTINGS.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if SETTINGS.smtp_username:
                smtp.login(SETTINGS.smtp_username, SETTINGS.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        raise EmailError(f"SMTP send failed: {error}") from error


# ------------------------------------------------------------------ digest

def _pending_unnotified(connection: sqlite3.Connection) -> list[dict]:
    pending = []
    for kind, select in _PENDING_SQL.items():
        rows = connection.execute(
            f"{select} WHERE event_id = ? AND approved = 0 AND active = 1 "
            "AND deleted_at IS NULL AND notified_at IS NULL ORDER BY id",
            (SETTINGS.event_id,)
        ).fetchall()
        pending += [{"kind": kind, "id": r[0], "full_name": r[1], "email": r[2]}
                    for r in rows]
    return pending


def _claim_digest(now: int) -> tuple[list[dict], int | None] | None:
    """Reserve the next digest slot. Returns (accounts, previous_sent_at) or
    None when it is too early or nothing is pending. BEGIN IMMEDIATE makes
    the check-and-claim atomic across workers."""
    connection = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT last_sent_at FROM notification_state WHERE name = ?",
            (DIGEST_STATE,)
        ).fetchone()
        previous = row[0] if row else None
        if previous is not None and now - previous < SETTINGS.notify_interval_minutes * 60:
            connection.execute("ROLLBACK")
            return None
        accounts = _pending_unnotified(connection)
        if not accounts:
            connection.execute("ROLLBACK")
            return None
        connection.execute(
            "INSERT INTO notification_state (name, last_sent_at) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET last_sent_at = excluded.last_sent_at",
            (DIGEST_STATE, now)
        )
        connection.execute("COMMIT")
        return accounts, previous
    finally:
        connection.close()


def _release_claim(previous: int | None) -> None:
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        if previous is None:
            connection.execute("DELETE FROM notification_state WHERE name = ?",
                               (DIGEST_STATE,))
        else:
            connection.execute(
                "UPDATE notification_state SET last_sent_at = ? WHERE name = ?",
                (previous, DIGEST_STATE))


def _digest_body(accounts: list[dict]) -> str:
    base = SETTINGS.public_base_url
    lines = [
        f"{len(accounts)} new registration(s) are waiting for approval.",
        "",
        "Each Approve link works once and expires in 72 hours. It opens a",
        "confirmation page; you must be logged in as an admin who may approve.",
        "",
    ]
    for account in accounts:
        token = account_tokens.issue("approve", account["kind"], account["id"],
                                     APPROVE_LINK_TTL_SECONDS)
        lines += [
            f"- {account['full_name']} <{account['email']}> ({LABELS[account['kind']]})",
            f"  Approve: {base}/admin/approve-link?token={token}",
        ]
    lines += ["", f"All pending registrations: {base}/admin/dashboard#pending"]
    return "\n".join(lines)


def _mark_notified(accounts: list[dict]) -> None:
    with sqlite3.connect(DB_PATH, timeout=10) as connection:
        for kind in _PENDING_SQL:
            ids = [a["id"] for a in accounts if a["kind"] == kind]
            if ids:
                table = "contributors" if kind == "contributor" else "attendees"
                connection.execute(
                    f"UPDATE {table} SET notified_at = CURRENT_TIMESTAMP "
                    f"WHERE id IN ({','.join('?' for _ in ids)})", ids)


def send_pending_digest() -> int:
    """Email the super admin about new registrations if due.

    Returns the number of registrations included (0 if nothing was sent).
    """
    if not (SETTINGS.email_enabled and SETTINGS.admin_notify_email):
        logger.debug("Registration digest skipped: email is not configured")
        return 0

    claim = _claim_digest(int(time.time()))
    if claim is None:
        return 0
    accounts, previous = claim

    try:
        send_email(SETTINGS.admin_notify_email,
                   f"TAM: {len(accounts)} new registration(s) awaiting approval",
                   _digest_body(accounts))
    except EmailError:
        logger.exception("Registration digest email failed; will retry")
        _release_claim(previous)
        return 0

    _mark_notified(accounts)
    logger.info("Registration digest sent for %d account(s)", len(accounts))
    return len(accounts)


async def digest_loop() -> None:
    """Background task started with the app (see main.lifespan)."""
    if not SETTINGS.email_enabled or not SETTINGS.admin_notify_email:
        logger.warning(
            "Email is not configured (TAM_EMAIL_PROVIDER, TAM_EMAIL_FROM, "
            "TAM_ADMIN_NOTIFY_EMAIL); new registrations will only appear on "
            "the admin dashboard."
        )
        return
    while True:
        try:
            await asyncio.to_thread(send_pending_digest)
        except sqlite3.Error:
            logger.exception("Registration digest failed")
        await asyncio.sleep(LOOP_SECONDS)
