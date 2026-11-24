"""
TAM Smart Photos — Contributor Consent API.

Independent of attendee facial-recognition consent.
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from contributor_consent_service import (
    create_contributor_consent,
    verify_contributor_consent,
    CONTRIBUTOR_CONSENT_COOKIE,
    CONTRIBUTOR_CONSENT_MAX_AGE,
)

router = APIRouter(prefix="/api/contributor-consent")

TAM_ORIGIN = "https://tam-smartphotos.duckdns.org"


class ContributorConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agreed: bool


@router.post("/accept")
async def accept_contributor_consent(
    request: Request,
):
    """Record an explicit contributor acknowledgement."""

    if request.headers.get("origin") != TAM_ORIGIN:
        raise HTTPException(
            status_code=403,
            detail="Invalid request origin"
        )

    if request.headers.get("sec-fetch-site") not in (
        None, "same-origin"
    ):
        raise HTTPException(
            status_code=403,
            detail="Cross-site request rejected"
        )

    content_type = request.headers.get(
        "content-type", ""
    ).split(";")[0].strip().lower()

    if content_type != "application/json":
        raise HTTPException(
            status_code=415,
            detail="JSON request required"
        )

    # Parse JSON only after origin and content-type checks.
    from pydantic import ValidationError

    try:
        payload = await request.json()
        data = ContributorConsentRequest.model_validate(payload)

    except (ValueError, ValidationError, TypeError):
        raise HTTPException(
            status_code=422,
            detail="Invalid contributor consent request"
        )

    if data.agreed is not True:
        raise HTTPException(
            status_code=400,
            detail="Contributor acknowledgement is required"
        )

    if verify_contributor_consent(request):
        return JSONResponse(
            {
                "status": "already_accepted",
                "next": "/contributor/login"
            },
            headers={"Cache-Control": "no-store"}
        )

    token = create_contributor_consent()

    response = JSONResponse(
        {
            "status": "accepted",
            "next": "/contributor/login"
        },
        headers={"Cache-Control": "no-store"}
    )

    response.set_cookie(
        key=CONTRIBUTOR_CONSENT_COOKIE,
        value=token,
        max_age=CONTRIBUTOR_CONSENT_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/"
    )

    return response
