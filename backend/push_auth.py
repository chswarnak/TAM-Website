"""
TAM Smart Photos — Push subscription authentication.

Verify the recipient's identity before accepting a
Web Push subscription.

Never trust recipient IDs supplied by the browser.
"""

import sqlite3

from fastapi import HTTPException, Request
from database import DB_PATH


def require_push_admin(request: Request) -> int:
    """
    Return the authenticated Admin ID.

    Only active Admins with contributor approval
    permission may subscribe to approval notifications.
    """

    admin_id = request.session.get("admin_id")

    if not isinstance(admin_id, int) or isinstance(admin_id, bool):
        raise HTTPException(
            status_code=401,
            detail="Administrator login required"
        )

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        admin = connection.execute(
            """
            SELECT id, role, must_change_password
            FROM admins
            WHERE id = ?
              AND active = 1
              AND deleted_at IS NULL
            """,
            (admin_id,)
        ).fetchone()

        if admin is None:
            raise HTTPException(
                status_code=403,
                detail="Administrator account is inactive"
            )

        if admin["must_change_password"]:
            raise HTTPException(
                status_code=403,
                detail="Complete password setup first"
            )

        if admin["role"] == "super_admin":
            return admin_id

        if admin["role"] != "admin":
            raise HTTPException(
                status_code=403,
                detail="Administrator permission required"
            )

        permission = connection.execute(
            """
            SELECT can_approve_contributors
            FROM organizer_permissions
            WHERE admin_id = ?
            """,
            (admin_id,)
        ).fetchone()

        if (
            permission is None
            or not permission["can_approve_contributors"]
        ):
            raise HTTPException(
                status_code=403,
                detail="Contributor approval permission required"
            )

    return admin_id


# TAM-PUSH-CONTRIBUTOR-AUTH-V1
def require_push_contributor(request: Request) -> int:
    """
    Identify a contributor using a valid registration cookie
    or an existing authenticated contributor session.

    Never accept a contributor ID directly from the browser.
    """
    import hashlib
    import time

    token = request.cookies.get("tam_contributor_registration")

    if token:
        token_hash = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()

        now = int(time.time())

        with sqlite3.connect(DB_PATH) as connection:
            contributor = connection.execute(
                """
                SELECT c.id
                FROM contributor_registration_tokens t
                JOIN contributors c ON c.id = t.contributor_id
                WHERE t.token_hash = ?
                  AND t.expires_at > ?
                  AND t.revoked_at IS NULL
                  AND c.event_id = ?
                  AND c.active = 1
                  AND c.deleted_at IS NULL
                """,
                (token_hash, now, "tam-diwali-2026")
            ).fetchone()

        if contributor is not None:
            return contributor[0]

    contributor_id = request.session.get("contributor_id")

    if not isinstance(contributor_id, int) or isinstance(contributor_id, bool):
        raise HTTPException(
            status_code=401,
            detail="Contributor authentication required"
        )

    with sqlite3.connect(DB_PATH) as connection:
        contributor = connection.execute(
            """
            SELECT id
            FROM contributors
            WHERE id = ?
              AND event_id = ?
              AND active = 1
              AND deleted_at IS NULL
              AND approved = 1
            """,
            (contributor_id, "tam-diwali-2026")
        ).fetchone()

    if contributor is None:
        raise HTTPException(
            status_code=403,
            detail="Contributor account is inactive or not approved"
        )

    return contributor_id
