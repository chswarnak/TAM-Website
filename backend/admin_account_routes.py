"""Admin actions on contributor and attendee accounts."""

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

import accounts
from admin_auth import require_admin_permission

logger = logging.getLogger(__name__)
router = APIRouter()

APPROVE_PERMISSION = "can_approve_contributors"


@router.post("/admin/approve/{contributor_id}")
def approve_contributor(
    contributor_id: int,
    request: Request,
    csrf_token: str = Form(...)
):
    admin_id = require_admin_permission(request, APPROVE_PERMISSION, csrf_token)
    if accounts.approve("contributor", [contributor_id], admin_id) == 0:
        raise HTTPException(status_code=404,
                            detail="Contributor not found for this event")
    logger.info("Admin %s approved contributor %s", admin_id, contributor_id)
    return RedirectResponse(url="/admin/contributors/manage", status_code=303)


def _contributor_json(pending_only: bool) -> dict:
    rows = accounts.list_accounts("contributor", pending_only=pending_only)
    return {
        "total": len(rows),
        "contributors": [
            {
                "id": row["id"],
                "name": row["full_name"],
                "email": row["email"],
                "mobile_number": accounts.mask_mobile(row["mobile_number"]),
                "status": "approved" if row["approved"] else "pending",
            }
            for row in rows
        ],
    }


@router.get("/admin/contributors")
def list_contributors(request: Request):
    require_admin_permission(request, APPROVE_PERMISSION)
    return _contributor_json(pending_only=False)


@router.get("/admin/contributors/pending")
def list_pending_contributors(request: Request):
    require_admin_permission(request, APPROVE_PERMISSION)
    return _contributor_json(pending_only=True)


@router.post("/admin/accounts/{kind}/{account_id}/set-login")
def set_account_login(
    kind: str,
    account_id: int,
    request: Request,
    csrf_token: str = Form(...),
    email: str = Form(...),
    username: str = Form(""),
):
    """Give an existing (pre-email) account its login email/username."""
    admin_id = require_admin_permission(request, APPROVE_PERMISSION, csrf_token)
    if kind not in accounts.KINDS:
        raise HTTPException(status_code=404, detail="Unknown account type")
    try:
        accounts.set_login(kind, account_id, email, username or None)
    except accounts.AccountError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error
    logger.info("Admin %s set login identifiers for %s %s", admin_id, kind, account_id)
    return RedirectResponse(url=_manage_page(kind), status_code=303)


def _manage_page(kind: str) -> str:
    return "/admin/contributors/manage" if kind == "contributor" else "/admin/dashboard"
