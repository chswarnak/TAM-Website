import pytest

import main
from client_helpers import ORIGIN, account_login, admin_login, new_client
from fakes import FakeS3, make_photo
from helpers import create_admin, create_contributor, create_event, db
from settings import SETTINGS

REGISTRATION = {
    "full_name": "Asha Rao",
    "email": "Asha@Example.com",
    "username": "asha_r",
    "mobile_number": "514-555-0100",
    "password": "contrib-pass-1",
}


def register(client, kind="contributor", **overrides):
    return client.post(f"/{kind}/register", json={**REGISTRATION, **overrides},
                       headers=ORIGIN)


def test_registration_is_pending_until_approved():
    client = new_client()
    response = register(client)
    assert response.status_code == 200
    assert response.json()["status"] == "pending_approval"

    login = account_login(client, "contributor", "asha@example.com", "contrib-pass-1")
    assert login.status_code == 403
    assert "approval" in login.json()["detail"].lower()


@pytest.mark.parametrize("overrides", [
    {"email": "not-an-email"},
    {"email": ""},
    {"username": "has space"},
    {"username": "has@sign"},
    {"full_name": "   "},
    {"password": "short"},
])
def test_registration_validation(overrides):
    assert register(new_client(), **overrides).status_code == 400


def test_email_and_username_are_unique_but_names_are_not():
    client = new_client()
    assert register(client).status_code == 200
    assert register(client, email="ASHA@example.com", username=None).status_code == 409
    assert register(client, email="other@example.com", username="ASHA_R").status_code == 409
    # Same display name, different identifiers: allowed.
    assert register(client, email="asha2@example.com", username=None).status_code == 200


def test_login_with_email_or_username_but_never_full_name():
    create_contributor(name="Asha Rao", email="asha@example.com", username="asha_r")
    client = new_client()
    assert account_login(client, "contributor", "ASHA@example.com",
                         "contrib-pass-1").status_code == 200
    assert account_login(client, "contributor", "asha_r",
                         "contrib-pass-1").status_code == 200
    assert account_login(client, "contributor", "Asha Rao",
                         "contrib-pass-1").status_code == 401


def test_legacy_id_login_is_gone():
    contributor_id = create_contributor()
    response = new_client().post("/contributor/login", json={
        "contributor_id": contributor_id, "password": "contrib-pass-1"})
    assert response.status_code in (401, 422)


def test_upload_folder_uses_account_id_not_name(monkeypatch, tmp_path):
    create_event()
    contributor_id = create_contributor(name="../../../../tmp/evil")
    s3 = FakeS3()
    monkeypatch.setattr(main, "upload_photo_to_s3", s3.upload_photo_to_s3)
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    client = new_client()
    account_login(client, "contributor", "asha@example.com", "contrib-pass-1")

    response = client.post("/upload", files={
        "file": ("p.jpg", make_photo((255, 0, 0)), "image/jpeg")})

    assert response.status_code == 200, response.text
    saved = list((tmp_path / "uploads").rglob("*.jpg"))
    assert len(saved) == 1
    assert saved[0].parent == (tmp_path / "uploads" / SETTINGS.event_id
                               / f"contributor-{contributor_id:03d}")


def test_super_admin_sets_login_for_existing_contributor():
    create_admin()
    with db() as conn:  # a pre-migration contributor: no email/username
        conn.execute(
            "INSERT INTO contributors (name, event_id, approved, password_hash) "
            "VALUES ('Old Timer', ?, 1, ?)",
            (SETTINGS.event_id, main.password_hasher.hash("old-password-1")))
        legacy_id = conn.execute("SELECT max(id) FROM contributors").fetchone()[0]

    admin = new_client()
    csrf = admin_login(admin)
    response = admin.post(f"/admin/accounts/contributor/{legacy_id}/set-login", data={
        "csrf_token": csrf, "email": "old.timer@example.com", "username": "oldtimer"},
        follow_redirects=False)
    assert response.status_code == 303

    client = new_client()
    assert account_login(client, "contributor", "oldtimer",
                         "old-password-1").status_code == 200


def test_set_login_rejects_duplicate_email():
    create_admin()
    create_contributor(email="taken@example.com")
    other = create_contributor(email="free@example.com")
    admin = new_client()
    csrf = admin_login(admin)
    response = admin.post(f"/admin/accounts/contributor/{other}/set-login", data={
        "csrf_token": csrf, "email": "TAKEN@example.com", "username": ""})
    assert response.status_code == 409
