"""
TAM Smart Photos — Web Push subscription API.

Security requirements:
- Admin subscriptions require authenticated Admin sessions.
- Admins must have contributor approval permission.
- Pending contributors use valid registration tokens.
- Browsers cannot choose another recipient's account ID.
- Subscription changes require origin and CSRF protection.
- Disabled or deleted accounts cannot receive notifications.
"""

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/push", tags=["Web Push"])


@router.get("/health", include_in_schema=False)
def push_health():
    """Check that the push notification API is installed."""
    return {"status": "ready"}


# TAM-WEBPUSH-PUBLIC-KEY-V1
@router.get("/public-key")
def get_push_public_key():
    """Return the public VAPID key for browser subscriptions."""
    import base64
    from pathlib import Path
    from cryptography.hazmat.primitives.serialization import (
        load_pem_public_key,
        Encoding,
        PublicFormat,
    )

    key_path = Path(__file__).resolve().parent / (
        ".push-secrets/vapid_public.pem"
    )

    public_key = load_pem_public_key(key_path.read_bytes())

    raw_key = public_key.public_bytes(
        encoding=Encoding.X962,
        format=PublicFormat.UncompressedPoint,
    )

    browser_key = base64.urlsafe_b64encode(
        raw_key
    ).rstrip(b"=").decode("ascii")

    return {"publicKey": browser_key}


# TAM-PUSH-SUBSCRIBE-V1
from pydantic import BaseModel, ConfigDict, Field
from push_security import verify_push_request_origin
from push_auth import require_push_admin, require_push_contributor
from push_storage import save_push_subscription


class PushKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=256)
    auth: str = Field(min_length=1, max_length=256)


class PushSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient_type: str
    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeys


@router.post("/subscribe")
def subscribe_to_push(
    data: PushSubscriptionRequest,
    request: Request,
):
    """Register a browser for authenticated TAM notifications."""

    verify_push_request_origin(request)

    if data.recipient_type == "admin":
        recipient_id = require_push_admin(request)

    elif data.recipient_type == "contributor":
        recipient_id = require_push_contributor(request)

    else:
        raise HTTPException(
            status_code=422,
            detail="Invalid notification recipient type"
        )

    try:
        result = save_push_subscription(
            recipient_type=data.recipient_type,
            recipient_id=recipient_id,
            endpoint=data.endpoint,
            p256dh=data.keys.p256dh,
            auth=data.keys.auth,
        )

    except ValueError:
        raise HTTPException(
            status_code=409,
            detail="Subscription ownership conflict"
        )

    return {
        "status": "subscribed",
        "action": result,
    }
