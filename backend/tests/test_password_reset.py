import dataclasses
import re

import pytest

import account_routes
import notifications
from client_helpers import ORIGIN, account_login, admin_login, new_client
from helpers import create_admin, create_attendee, create_contributor

TEMP_RE = re.compile(r'id="temporary-password">([^<]+)<')


def _admin_reset(kind, account_id):
    admin = new_client()
    csrf = admin_login(admin)
    return admin.post(f"/admin/accounts/{kind}/{account_id}/reset-password",
                      data={"csrf_token": csrf})


def test_admin_reset_forces_password_change():
    create_admin()
    attendee = create_attendee()
    page = _admin_reset("attendee", attendee)
    assert page.status_code == 200
    temporary = TEMP_RE.search(page.text).group(1)

    client = new_client()
    assert account_login(client, "attendee", "ravi@example.com",
                         "attendee-pass-1").status_code == 401
    login = account_login(client, "attendee", "ravi@example.com", temporary)
    assert login.json()["redirect"] == "/account/change-password"

    # Nothing else works until the password is changed.
    assert client.get("/api/find-my-photos/consent-status").status_code == 403
    assert client.get("/find-my-photos", follow_redirects=False).headers[
        "location"] == "/account/change-password"

    changed = client.post("/account/change-password", headers=ORIGIN, data={
        "current_password": temporary, "new_password": "brand-new-pass-1",
        "confirm_password": "brand-new-pass-1"}, follow_redirects=False)
    assert changed.status_code == 303
    assert client.get("/api/find-my-photos/consent-status").status_code == 200


def test_change_password_rejects_wrong_current_password():
    create_admin()
    contributor = create_contributor()
    temporary = TEMP_RE.search(_admin_reset("contributor", contributor).text).group(1)
    client = new_client()
    account_login(client, "contributor", "asha@example.com", temporary)
    response = client.post("/account/change-password", headers=ORIGIN, data={
        "current_password": "nope", "new_password": "brand-new-pass-1",
        "confirm_password": "brand-new-pass-1"})
    assert response.status_code == 400


def test_admin_reset_requires_permission():
    create_admin(username="helper", password="helper-password-1", role="admin")
    attendee = create_attendee()
    admin = new_client()
    csrf = admin_login(admin, "helper", "helper-password-1")
    response = admin.post(f"/admin/accounts/attendee/{attendee}/reset-password",
                          data={"csrf_token": csrf})
    assert response.status_code == 403


def test_forgot_password_is_unavailable_without_email():
    assert new_client().get("/account/forgot-password").status_code == 404


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    configured = dataclasses.replace(
        notifications.SETTINGS, email_provider="smtp", email_from="tam@example.com",
        public_base_url="https://tam.example")
    monkeypatch.setattr(notifications, "SETTINGS", configured)
    monkeypatch.setattr(account_routes, "SETTINGS", configured)
    monkeypatch.setattr(notifications, "send_email",
                        lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def _forgot(email, kind="attendee"):
    return new_client().post("/account/forgot-password", headers=ORIGIN,
                             data={"kind": kind, "email": email})


def test_self_service_reset_with_single_use_link(outbox):
    create_attendee()
    known = _forgot("ravi@example.com")
    unknown = _forgot("nobody@example.com")
    assert known.status_code == unknown.status_code == 200
    assert known.text == unknown.text  # no account enumeration
    assert len(outbox) == 1 and outbox[0][0] == "ravi@example.com"

    token = re.search(r"reset-password\?token=(\S+)", outbox[0][2]).group(1)
    client = new_client()
    assert client.get(f"/account/reset-password?token={token}").status_code == 200
    done = client.post("/account/reset-password", headers=ORIGIN, data={
        "token": token, "new_password": "fresh-password-1",
        "confirm_password": "fresh-password-1"})
    assert done.status_code == 200
    assert account_login(new_client(), "attendee", "ravi@example.com",
                         "fresh-password-1").status_code == 200

    reused = client.post("/account/reset-password", headers=ORIGIN, data={
        "token": token, "new_password": "other-password-1",
        "confirm_password": "other-password-1"})
    assert reused.status_code == 410


def test_forgot_password_is_rate_limited(outbox):
    create_attendee()
    for _ in range(5):
        _forgot("ravi@example.com")
    assert _forgot("ravi@example.com").status_code == 429
    assert len(outbox) == 5


def test_attendee_management_page_lists_attendees():
    create_admin()
    create_attendee(full_name="Visible Person")
    admin = new_client()
    admin_login(admin)
    page = admin.get("/admin/attendees/manage")
    assert page.status_code == 200
    assert "Visible Person" in page.text
    assert "/reset-password" in page.text
