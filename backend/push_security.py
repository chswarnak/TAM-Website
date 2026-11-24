"""
TAM Smart Photos — Web Push request security.

This module validates request origins for subscription-changing
API requests.

Account authentication and permission checks are enforced
separately by the subscription API.
"""

from fastapi import HTTPException, Request

TAM_ORIGIN = "https://tam-smartphotos.duckdns.org"


def verify_push_request_origin(request: Request) -> None:
    """Reject cross-origin subscription modification requests."""

    origin = request.headers.get("origin")

    if origin != TAM_ORIGIN:
        raise HTTPException(
            status_code=403,
            detail="Invalid request origin"
        )

    fetch_site = request.headers.get("sec-fetch-site")

    if fetch_site is not None and fetch_site != "same-origin":
        raise HTTPException(
            status_code=403,
            detail="Cross-site requests are not permitted"
        )

    content_type = request.headers.get(
        "content-type", ""
    ).split(";", 1)[0].strip().lower()

    if content_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail="JSON content type required"
        )
