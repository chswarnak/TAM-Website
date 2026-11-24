"""Admin actions on contributor and attendee accounts."""

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import account_tokens
import accounts
from admin_auth import create_csrf_token, require_admin_permission

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")

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


# ------------------------------------------------------- one-click approval

def _account_summary(kind: str, account_id: int) -> dict | None:
    for row in accounts.list_accounts(kind, pending_only=True):
        if row["id"] == account_id:
            return {**row, "kind": kind, "label": kind.title()}
    return None


@router.get("/admin/approve-link", response_class=HTMLResponse)
def approve_link_page(request: Request, token: str = ""):
    """Confirmation page for an emailed Approve link. Never approves on GET
    (mail scanners prefetch links)."""
    if not isinstance(request.session.get("admin_id"), int):
        return RedirectResponse(url="/admin/login", status_code=303)
    admin_id = require_admin_permission(request, APPROVE_PERMISSION)

    target = account_tokens.peek(token, "approve")
    account = _account_summary(*target) if target else None
    return templates.TemplateResponse(
        request=request, name="admin_approve_link.html",
        status_code=200 if account else 410,
        context={"account": account, "token": token,
                 "csrf_token": create_csrf_token(
                     admin_id, request.session.get("csrf_nonce", ""))},
    )


@router.post("/admin/approve-link")
def approve_link_confirm(request: Request, token: str = Form(...),
                         csrf_token: str = Form(...)):
    admin_id = require_admin_permission(request, APPROVE_PERMISSION, csrf_token)
    target = account_tokens.consume(token, "approve")
    if target is None:
        raise HTTPException(status_code=410,
                            detail="This approval link was already used or has expired.")
    kind, account_id = target
    approved = accounts.approve(kind, [account_id], admin_id)
    logger.info("Admin %s approved %s %s via email link (changed=%d)",
                admin_id, kind, account_id, approved)
    return RedirectResponse(url="/admin/dashboard#pending", status_code=303)


# ------------------------------------------------------------ bulk approval

@router.post("/admin/accounts/approve-selected")
def approve_selected(request: Request, csrf_token: str = Form(...),
                     selected: list[str] = Form(default=[])):
    """selected values look like "attendee:12" or "contributor:3"."""
    admin_id = require_admin_permission(request, APPROVE_PERMISSION, csrf_token)
    ids: dict[str, list[int]] = {kind: [] for kind in accounts.KINDS}
    for value in selected:
        kind, _, raw_id = value.partition(":")
        if kind not in ids or not raw_id.isdigit():
            raise HTTPException(status_code=400, detail=f"Invalid selection: {value!r}")
        ids[kind].append(int(raw_id))
    total = sum(accounts.approve(kind, kind_ids, admin_id)
                for kind, kind_ids in ids.items())
    logger.info("Admin %s approved %d selected account(s)", admin_id, total)
    return RedirectResponse(url="/admin/dashboard#pending", status_code=303)


@router.post("/admin/accounts/approve-all")
def approve_all_pending(request: Request, csrf_token: str = Form(...)):
    admin_id = require_admin_permission(request, APPROVE_PERMISSION, csrf_token)
    total = 0
    for kind in accounts.KINDS:
        pending = [row["id"] for row in accounts.list_accounts(kind, pending_only=True)]
        total += accounts.approve(kind, pending, admin_id)
    logger.info("Admin %s approved all %d pending account(s)", admin_id, total)
    return RedirectResponse(url="/admin/dashboard#pending", status_code=303)
