"""Request checks shared by routers: origin, HTTPS and client address."""

import ipaddress

from fastapi import HTTPException, Request

from settings import SETTINGS

LOOPBACK = {"127.0.0.1", "::1"}


def require_same_origin(request: Request, strict: bool = False) -> None:
    """Reject cross-site requests.

    strict=True also rejects requests without an Origin header (used for
    biometric endpoints, which are only ever called from our own pages).
    """
    origin = request.headers.get("origin")
    if origin is None and not strict:
        return
    if origin != SETTINGS.allowed_origin:
        raise HTTPException(status_code=403, detail="Invalid request origin.")


def require_https(request: Request) -> None:
    """Behind nginx, the public scheme arrives in X-Forwarded-Proto."""
    if SETTINGS.require_https and request.headers.get("x-forwarded-proto") != "https":
        raise HTTPException(status_code=403, detail="Secure HTTPS connection required.")


def client_ip(request: Request) -> str:
    """The real client address.

    X-Real-IP is trusted only when the direct peer is the local reverse
    proxy; otherwise anyone could spoof it to dodge rate limits.
    """
    peer = request.client.host if request.client else ""
    if peer in LOOPBACK:
        forwarded = request.headers.get("x-real-ip", "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            return peer
    return peer or "unknown"
