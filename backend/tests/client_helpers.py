"""HTTP helpers for driving the app in tests."""

import re

from fastapi.testclient import TestClient

import main

CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
ORIGIN = {"Origin": "http://testserver"}


def new_client() -> TestClient:
    return TestClient(main.app)


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "no csrf_token field on page"
    return match.group(1)


def admin_login(client: TestClient, username="boss",
                password="super-secret-pass") -> str:
    """Log in an admin and return a CSRF token for the session."""
    response = client.post("/admin/login",
                           data={"username": username, "password": password},
                           follow_redirects=False)
    assert response.status_code == 303, response.text
    # Redirects to the change-password page while a temporary password is set.
    page = client.get("/admin/contributors/manage")
    return csrf_from(page.text)


def account_login(client: TestClient, kind: str, identifier: str, password: str):
    return client.post(f"/{kind}/login",
                       json={"identifier": identifier, "password": password},
                       headers=ORIGIN)
