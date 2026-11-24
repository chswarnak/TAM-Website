"""Registration, login and logout pages/APIs for contributors and attendees."""

import logging

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import account_tokens
import accounts
import notifications
import rate_limit
from http_security import client_ip, require_same_origin
from settings import SETTINGS

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")

PAGES = {
    "contributor": {
        "label": "Contributor",
        "purpose": "Register to contribute photographs to TAM events.",
        "login_purpose": "Sign in to upload your event photos.",
        "home": "/contributor/dashboard",
    },
    "attendee": {
        "label": "Attendee",
        "purpose": "Register to find the event photos you appear in.",
        "login_purpose": "Sign in to find your event photos.",
        "home": "/find-my-photos",
    },
}


class Registration(BaseModel):
    full_name: str
    email: str
    username: str | None = None
    mobile_number: str | None = None
    password: str


class Login(BaseModel):
    identifier: str
    password: str


def too_many_attempts(wait: int) -> JSONResponse:
    minutes = max(1, round(wait / 60))
    return JSONResponse(
        {"detail": f"Too many failed attempts. Try again in about {minutes} minute(s)."},
        status_code=429,
        headers={"Retry-After": str(wait)},
    )


def _register_page(kind: str):
    def page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="account_register.html",
            context={"kind": kind, "label": PAGES[kind]["label"],
                     "purpose": PAGES[kind]["purpose"],
                     "mobile_required": kind == "contributor"},
        )
    return page


def _register_api(kind: str):
    def register(data: Registration, request: Request,
                 background_tasks: BackgroundTasks):
        require_same_origin(request)
        try:
            account_id = accounts.register(
                kind, data.full_name, data.email, data.username,
                data.mobile_number, data.password,
            )
        except accounts.AccountError as error:
            raise HTTPException(status_code=error.status_code,
                                detail=str(error)) from error
        logger.info("New %s registration %s (pending approval)", kind, account_id)
        # Send right away if the digest interval allows; otherwise the
        # background loop includes it in the next digest.
        background_tasks.add_task(notifications.send_pending_digest)
        return {"message": "Registration submitted", "status": "pending_approval"}
    return register


def _login_page(kind: str):
    def page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="account_login.html",
            context={"kind": kind, "label": PAGES[kind]["label"],
                     "purpose": PAGES[kind]["login_purpose"],
                     "email_enabled": SETTINGS.email_enabled},
        )
    return page


def _login_api(kind: str):
    def login(data: Login, request: Request):
        require_same_origin(request)
        ip = client_ip(request)
        wait = rate_limit.retry_after(kind, ip, data.identifier)
        if wait:
            return too_many_attempts(wait)
        try:
            account = accounts.authenticate(kind, data.identifier, data.password)
        except accounts.LoginError as error:
            if type(error) is accounts.LoginError:  # wrong credentials only
                rate_limit.record_failure(kind, ip, data.identifier)
            return JSONResponse({"detail": error.message},
                                status_code=error.status_code)

        rate_limit.clear_identifier(kind, ip, data.identifier)
        accounts.start_session(request, kind, account["id"])
        redirect = ("/account/change-password" if account["must_change_password"]
                    else PAGES[kind]["home"])
        return {"message": "Login successful", "redirect": redirect,
                "must_change_password": bool(account["must_change_password"])}
    return login


def _logout(kind: str):
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url=f"/{kind}/login", status_code=303)
    return logout


for _kind in accounts.KINDS:
    router.add_api_route(f"/{_kind}/register", _register_page(_kind),
                         methods=["GET"], response_class=HTMLResponse)
    router.add_api_route(f"/{_kind}/register", _register_api(_kind), methods=["POST"])
    router.add_api_route(f"/{_kind}/login", _login_page(_kind),
                         methods=["GET"], response_class=HTMLResponse)
    router.add_api_route(f"/{_kind}/login", _login_api(_kind), methods=["POST"])
    router.add_api_route(f"/{_kind}/logout", _logout(_kind), methods=["POST"])


# ---------------------------------------------------------------- passwords

RESET_LINK_TTL_SECONDS = 3600
FORGOT_SCOPE = "forgot-password"
FORGOT_MESSAGE = ("If an approved account uses that email, we have sent it a "
                  "password reset link. The link works once and expires in 1 hour.")


def _password_page(request: Request, mode: str, status_code: int = 200, **context):
    return templates.TemplateResponse(
        request=request, name="account_password.html", status_code=status_code,
        context={"mode": mode, **context},
    )


@router.get("/account/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    kind = accounts.logged_in_kind(request)
    account = accounts.session_account(request, kind) if kind else None
    if account is None:
        return RedirectResponse(url="/attendee/login", status_code=303)
    return _password_page(request, "change",
                          forced=bool(account["must_change_password"]))


@router.post("/account/change-password", response_class=HTMLResponse)
def change_password(request: Request, current_password: str = Form(...),
                    new_password: str = Form(...), confirm_password: str = Form(...)):
    require_same_origin(request)
    kind = accounts.logged_in_kind(request)
    account = accounts.session_account(request, kind) if kind else None
    if account is None:
        raise HTTPException(status_code=401, detail="Please log in first.")

    error = None
    if not accounts.verify_password(kind, account["id"], current_password):
        error = "Your current password is incorrect."
    elif new_password != confirm_password:
        error = "The new passwords do not match."
    elif new_password == current_password:
        error = "Please choose a different password."
    else:
        try:
            accounts.change_password(kind, account["id"], new_password)
        except accounts.AccountError as exc:
            error = str(exc)
    if error:
        return _password_page(request, "change", status_code=400, error=error,
                              forced=bool(account["must_change_password"]))

    logger.info("%s %s changed their password", kind, account["id"])
    return RedirectResponse(url=PAGES[kind]["home"], status_code=303)


def _require_email_enabled() -> None:
    if not SETTINGS.email_enabled:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/account/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request, kind: str = "attendee"):
    _require_email_enabled()
    if kind not in accounts.KINDS:
        kind = "attendee"
    return _password_page(request, "forgot", kind=kind)


@router.post("/account/forgot-password", response_class=HTMLResponse)
def forgot_password(request: Request, kind: str = Form(...), email: str = Form(...)):
    _require_email_enabled()
    require_same_origin(request)
    if kind not in accounts.KINDS:
        raise HTTPException(status_code=400, detail="Unknown account type")

    ip = client_ip(request)
    wait = rate_limit.retry_after(FORGOT_SCOPE, ip, email)
    if wait:
        return too_many_attempts(wait)
    rate_limit.record_failure(FORGOT_SCOPE, ip, email)

    account_id = accounts.find_active_by_email(kind, email)
    if account_id is not None:
        token = account_tokens.issue("reset", kind, account_id, RESET_LINK_TTL_SECONDS)
        link = f"{SETTINGS.public_base_url}/account/reset-password?token={token}"
        try:
            notifications.send_email(
                email.strip().lower(), "TAM password reset",
                "Someone asked to reset the password for your TAM account.\n\n"
                f"Reset it here (works once, expires in 1 hour):\n{link}\n\n"
                "If this wasn't you, ignore this email; your password is unchanged.")
        except notifications.EmailError:
            logger.exception("Password reset email failed for %s %s", kind, account_id)
    # Same response either way, so the form can't reveal which emails exist.
    return _password_page(request, "message", message=FORGOT_MESSAGE)


@router.get("/account/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = ""):
    if account_tokens.peek(token, "reset") is None:
        return _password_page(request, "invalid", status_code=410)
    return _password_page(request, "reset", token=token)


@router.post("/account/reset-password", response_class=HTMLResponse)
def reset_password(request: Request, token: str = Form(...),
                   new_password: str = Form(...), confirm_password: str = Form(...)):
    require_same_origin(request)
    # Validate first so a typo doesn't burn the single-use token.
    error = None
    if new_password != confirm_password:
        error = "The passwords do not match."
    else:
        try:
            accounts.validate_password(new_password)
        except accounts.AccountError as exc:
            error = str(exc)
    if error:
        return _password_page(request, "reset", status_code=400, token=token, error=error)

    target = account_tokens.consume(token, "reset")
    if target is None:
        return _password_page(request, "invalid", status_code=410)
    kind, account_id = target
    accounts.change_password(kind, account_id, new_password)
    logger.info("%s %s reset their password by email link", kind, account_id)
    return _password_page(request, "message", kind=kind,
                          message="Your password has been changed. You can now log in.")
