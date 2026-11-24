"""
TAM Smart Photos — Web Push subscription storage.

Browser subscription endpoints are sensitive.
Never expose them through public API responses or logs.

Recipient identity must be verified before storage operations.
"""

import sqlite3

from database import DB_PATH


def count_push_subscriptions(
    recipient_type: str,
    recipient_id: int,
) -> int:
    """Count active subscriptions belonging to a verified recipient."""

    if recipient_type not in ("admin", "contributor"):
        raise ValueError("Invalid recipient type")

    if (
        not isinstance(recipient_id, int)
        or isinstance(recipient_id, bool)
        or recipient_id <= 0
    ):
        raise ValueError("Invalid recipient ID")

    with sqlite3.connect(DB_PATH) as connection:
        result = connection.execute(
            """
            SELECT COUNT(*)
            FROM push_subscriptions
            WHERE recipient_type = ?
              AND recipient_id = ?
              AND active = 1
            """,
            (recipient_type, recipient_id)
        ).fetchone()

    return result[0]


# TAM-PUSH-SAVE-SUBSCRIPTION-V1
def save_push_subscription(
    recipient_type: str,
    recipient_id: int,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> str:
    """
    Save a subscription for an already authenticated recipient.

    Never transfer an existing endpoint to another account.
    """

    if recipient_type not in ("admin", "contributor"):
        raise ValueError("Invalid recipient type")

    if (
        not isinstance(recipient_id, int)
        or isinstance(recipient_id, bool)
        or recipient_id <= 0
    ):
        raise ValueError("Invalid recipient ID")

    from push_validation import (
        validate_push_endpoint,
        validate_push_key,
    )

    endpoint = validate_push_endpoint(endpoint)
    p256dh = validate_push_key(p256dh, "p256dh", 65)
    auth = validate_push_key(auth, "auth", 16)

    with sqlite3.connect(DB_PATH, timeout=15) as connection:

        # Serialize competing writes to prevent ownership races.
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            """
            SELECT recipient_type, recipient_id
            FROM push_subscriptions
            WHERE endpoint = ?
            """,
            (endpoint,)
        ).fetchone()

        if existing is not None:

            if (
                existing[0] != recipient_type
                or existing[1] != recipient_id
            ):
                raise ValueError(
                    "Subscription belongs to another account"
                )

            connection.execute(
                """
                UPDATE push_subscriptions
                SET p256dh = ?,
                    auth = ?,
                    active = 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE endpoint = ?
                """,
                (p256dh, auth, endpoint)
            )

            return "updated"

        connection.execute(
            """
            INSERT INTO push_subscriptions
                (
                    recipient_type,
                    recipient_id,
                    endpoint,
                    p256dh,
                    auth
                )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                recipient_type,
                recipient_id,
                endpoint,
                p256dh,
                auth
            )
        )

    return "created"
