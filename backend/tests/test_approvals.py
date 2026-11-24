import dataclasses
import logging
import re
import time

import pytest

import account_tokens
import notifications
from client_helpers import ORIGIN, admin_login, csrf_from, new_client
from helpers import create_admin, create_attendee, create_contributor, db


def _approved(table, account_id):
    with db() as conn:
        return conn.execute(f"SELECT approved FROM {table} WHERE id = ?",
                            (account_id,)).fetchone()[0]


# ------------------------------------------------------------------ tokens

def test_token_is_single_use():
    token = account_tokens.issue("approve", "attendee", 7, ttl_seconds=60)
    assert account_tokens.peek(token, "approve") == ("attendee", 7)
    assert account_tokens.consume(token, "approve") == ("attendee", 7)
    assert account_tokens.consume(token, "approve") is None
    assert account_tokens.peek(token, "approve") is None


def test_token_expires(monkeypatch):
    token = account_tokens.issue("approve", "attendee", 7, ttl_seconds=60)
    real_time = time.time
    monkeypatch.setattr(account_tokens.time, "time", lambda: real_time() + 61)
    assert account_tokens.consume(token, "approve") is None


def test_token_purpose_and_signature_are_checked():
    token = account_tokens.issue("reset", "attendee", 7, ttl_seconds=60)
    assert account_tokens.consume(token, "approve") is None
    assert account_tokens.consume(token[:-2] + "xx", "reset") is None


# ------------------------------------------------------------------ digest

@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(notifications, "SETTINGS", dataclasses.replace(
        notifications.SETTINGS, email_provider="smtp", email_from="tam@example.com",
        admin_notify_email="boss@example.com", public_base_url="https://tam.example",
        notify_interval_minutes=5))
    monkeypatch.setattr(notifications, "send_email",
                        lambda to, subject, body: sent.append((to, subject, body)))
    return sent


def _register(kind, email):
    new_client().post(f"/{kind}/register", headers=ORIGIN, json={
        "full_name": f"Person {email}", "email": email,
        "mobile_number": "5145550100", "password": "long-enough-1"})


def test_digest_lists_all_new_registrations_once(outbox):
    _register("contributor", "c@example.com")
    _register("attendee", "a@example.com")

    assert notifications.send_pending_digest() == 2
    assert notifications.send_pending_digest() == 0

    assert len(outbox) == 1
    to, subject, body = outbox[0]
    assert to == "boss@example.com"
    assert "2 new" in subject
    assert "c@example.com" in body and "Contributor" in body
    assert "a@example.com" in body and "Attendee" in body
    assert body.count("https://tam.example/admin/approve-link?token=") == 2
    assert "https://tam.example/admin/dashboard#pending" in body


def test_digest_is_throttled(outbox, monkeypatch):
    _register("attendee", "first@example.com")
    notifications.send_pending_digest()
    _register("attendee", "second@example.com")

    assert notifications.send_pending_digest() == 0  # within the interval

    real_time = time.time
    monkeypatch.setattr(notifications.time, "time", lambda: real_time() + 301)
    assert notifications.send_pending_digest() == 1
    assert "second@example.com" in outbox[-1][2]
    assert "first@example.com" not in outbox[-1][2]


def test_failed_send_is_logged_and_retried(outbox, monkeypatch, caplog):
    _register("attendee", "a@example.com")

    def broken(*_):
        raise notifications.EmailError("SMTP server unreachable")

    monkeypatch.setattr(notifications, "send_email", broken)
    with caplog.at_level(logging.ERROR):
        assert notifications.send_pending_digest() == 0
    assert "SMTP server unreachable" in caplog.text

    monkeypatch.setattr(notifications, "send_email",
                        lambda *args: outbox.append(args))
    assert notifications.send_pending_digest() == 1


def test_no_email_configured_skips_digest(monkeypatch):
    monkeypatch.setattr(notifications, "SETTINGS", dataclasses.replace(
        notifications.SETTINGS, email_provider=""))
    _register("attendee", "a@example.com")
    assert notifications.send_pending_digest() == 0
    with db() as conn:
        assert conn.execute("SELECT notified_at FROM attendees").fetchone()[0] is None


# ----------------------------------------------------------- approve link

def test_approve_link_needs_admin_login_and_one_post():
    create_admin()
    attendee = create_attendee(approved=False)
    token = account_tokens.issue("approve", "attendee", attendee, ttl_seconds=600)

    anonymous = new_client().get(f"/admin/approve-link?token={token}",
                                 follow_redirects=False)
    assert anonymous.status_code == 303
    assert _approved("attendees", attendee) == 0

    admin = new_client()
    admin_login(admin)
    page = admin.get(f"/admin/approve-link?token={token}")
    assert page.status_code == 200
    assert _approved("attendees", attendee) == 0  # GET never approves

    response = admin.post("/admin/approve-link", data={
        "token": token, "csrf_token": csrf_from(page.text)}, follow_redirects=False)
    assert response.status_code == 303
    assert _approved("attendees", attendee) == 1

    reused = admin.post("/admin/approve-link", data={
        "token": token, "csrf_token": csrf_from(page.text)})
    assert reused.status_code == 410


def test_approve_link_respects_permissions():
    create_admin(username="helper", password="helper-password-1", role="admin")
    attendee = create_attendee(approved=False)
    token = account_tokens.issue("approve", "attendee", attendee, ttl_seconds=600)
    admin = new_client()
    admin_login(admin, "helper", "helper-password-1")

    assert admin.get(f"/admin/approve-link?token={token}").status_code == 403
    assert account_tokens.peek(token, "approve") == ("attendee", attendee)


# -------------------------------------------------------------- dashboard

def test_dashboard_shows_pending_and_approves_selected():
    create_admin()
    pending_c = create_contributor(email="c@example.com", approved=False)
    pending_a1 = create_attendee(email="a1@example.com", approved=False)
    pending_a2 = create_attendee(email="a2@example.com", approved=False)
    admin = new_client()
    admin_login(admin)

    page = admin.get("/admin/dashboard")
    assert re.search(r"Pending approvals:\s*<strong>3</strong>", page.text)

    response = admin.post("/admin/accounts/approve-selected", data={
        "csrf_token": csrf_from(page.text),
        "selected": [f"contributor:{pending_c}", f"attendee:{pending_a1}"]},
        follow_redirects=False)
    assert response.status_code == 303
    assert _approved("contributors", pending_c) == 1
    assert _approved("attendees", pending_a1) == 1
    assert _approved("attendees", pending_a2) == 0


def test_dashboard_approve_all_pending():
    create_admin()
    ids = [create_attendee(email=f"a{n}@example.com", approved=False) for n in range(3)]
    contributor = create_contributor(approved=False)
    admin = new_client()
    admin_login(admin)
    csrf = csrf_from(admin.get("/admin/dashboard").text)

    admin.post("/admin/accounts/approve-all", data={"csrf_token": csrf})

    assert all(_approved("attendees", i) for i in ids)
    assert _approved("contributors", contributor) == 1


def test_bulk_approve_requires_permission():
    create_admin(username="helper", password="helper-password-1", role="admin")
    attendee = create_attendee(approved=False)
    admin = new_client()
    csrf = admin_login(admin, "helper", "helper-password-1")
    response = admin.post("/admin/accounts/approve-all", data={"csrf_token": csrf})
    assert response.status_code == 403
    assert _approved("attendees", attendee) == 0
