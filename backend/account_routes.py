"""Registration, login and logout pages/APIs for contributors and attendees."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import accounts
from http_security import require_same_origin
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
        try:
            account = accounts.authenticate(kind, data.identifier, data.password)
        except accounts.LoginError as error:
            return JSONResponse({"detail": error.message},
                                status_code=error.status_code)

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
