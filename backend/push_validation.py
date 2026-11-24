"""
TAM Smart Photos — Web Push subscription validation.

Reject malformed browser subscriptions and unauthorized
push-service destinations before saving them.
"""

import base64
import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException


ALLOWED_PUSH_HOSTS = {
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
}

ALLOWED_PUSH_SUFFIXES = (
    ".push.apple.com",
)


def validate_push_endpoint(endpoint: str) -> str:
    """Allow only HTTPS endpoints from approved push services."""

    if not isinstance(endpoint, str):
        raise HTTPException(422, "Invalid push endpoint")

    if len(endpoint) > 2048:
        raise HTTPException(422, "Push endpoint is too long")

    if any(ord(char) < 33 for char in endpoint):
        raise HTTPException(422, "Invalid endpoint characters")

    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname

        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Invalid endpoint")

        host = host.lower().rstrip(".")

        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("IP endpoints are not permitted")

        allowed = (
            host in ALLOWED_PUSH_HOSTS
            or any(
                host.endswith(suffix)
                for suffix in ALLOWED_PUSH_SUFFIXES
            )
        )

        if not allowed:
            raise ValueError("Unapproved push service")

    except ValueError:
        raise HTTPException(
            422,
            "Unsupported or invalid browser push endpoint"
        )

    return endpoint


def validate_push_key(value: str, name: str, expected_size: int) -> str:
    """Validate Base64URL-encoded browser encryption keys."""

    if not isinstance(value, str) or len(value) > 256:
        raise HTTPException(422, f"Invalid {name} key")

    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value + padding)
    except Exception:
        raise HTTPException(422, f"Invalid {name} key")

    if len(decoded) != expected_size:
        raise HTTPException(422, f"Invalid {name} key length")

    return value
