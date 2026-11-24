"""
TAM Smart Photos — Web Push notification delivery.

Responsibilities:
- Load the server's VAPID private key.
- Send encrypted push messages.
- Keep notification destinations within the TAM website.

Authentication, recipient authorization, and subscription storage
will be handled separately.
"""

import json
from pathlib import Path
from urllib.parse import urlsplit

from pywebpush import webpush, WebPushException


VAPID_PRIVATE_KEY = Path(__file__).resolve().parent / (
    ".push-secrets/vapid_private.pem"
)

VAPID_CLAIMS = {
    "sub": "mailto:info@telugumontreal.org"
}


def safe_notification_path(value: str) -> str:
    """Allow only local website paths."""

    if not isinstance(value, str):
        return "/"

    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\\\" in value
        or any(ord(char) < 32 for char in value)
    ):
        return "/"

    parsed = urlsplit(value)

    if parsed.scheme or parsed.netloc:
        return "/"

    return value


def send_push_notification(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    title: str,
    body: str,
    url: str = "/",
    tag: str = "tam-notification",
) -> None:
    """Send one encrypted notification to a stored subscription."""

    if not VAPID_PRIVATE_KEY.is_file():
        raise RuntimeError("VAPID private key is missing.")

    payload = {
        "title": title[:100],
        "body": body[:200],
        "url": safe_notification_path(url),
        "tag": tag[:100],
    }

    subscription = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": p256dh,
            "auth": auth,
        },
    }

    webpush(
        subscription_info=subscription,
        data=json.dumps(payload),
        vapid_private_key=str(VAPID_PRIVATE_KEY),
        vapid_claims=VAPID_CLAIMS.copy(),
        ttl=3600,
        timeout=10,
    )
