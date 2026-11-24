from client_helpers import admin_login, new_client
from helpers import create_admin, create_contributor, db


def _approved(contributor_id: int) -> int:
    with db() as conn:
        return conn.execute("SELECT approved FROM contributors WHERE id = ?",
                            (contributor_id,)).fetchone()[0]


def test_super_admin_can_approve():
    create_admin()
    pending = create_contributor(approved=False)
    client = new_client()
    csrf = admin_login(client)

    response = client.post(f"/admin/approve/{pending}", data={"csrf_token": csrf},
                           follow_redirects=False)

    assert response.status_code == 303
    assert _approved(pending) == 1


def test_organizer_without_permission_cannot_approve():
    create_admin(username="helper", password="helper-password-1", role="admin")
    pending = create_contributor(approved=False)
    client = new_client()
    csrf = admin_login(client, "helper", "helper-password-1")

    response = client.post(f"/admin/approve/{pending}", data={"csrf_token": csrf})

    assert response.status_code == 403
    assert _approved(pending) == 0


def test_organizer_with_permission_can_approve():
    create_admin(username="helper", password="helper-password-1", role="admin",
                 can_approve=True)
    pending = create_contributor(approved=False)
    client = new_client()
    csrf = admin_login(client, "helper", "helper-password-1")

    response = client.post(f"/admin/approve/{pending}", data={"csrf_token": csrf},
                           follow_redirects=False)

    assert response.status_code == 303
    assert _approved(pending) == 1


def test_admin_with_temporary_password_cannot_approve():
    create_admin(must_change=True)
    pending = create_contributor(approved=False)
    client = new_client()
    csrf = admin_login(client)  # lands on the change-password page

    response = client.post(f"/admin/approve/{pending}", data={"csrf_token": csrf})

    assert response.status_code == 403
    assert _approved(pending) == 0


def test_disabled_admin_with_live_session_cannot_approve():
    admin_id = create_admin()
    pending = create_contributor(approved=False)
    client = new_client()
    csrf = admin_login(client)
    with db() as conn:
        conn.execute("UPDATE admins SET active = 0 WHERE id = ?", (admin_id,))

    response = client.post(f"/admin/approve/{pending}", data={"csrf_token": csrf})

    assert response.status_code == 403
    assert _approved(pending) == 0


def test_contributor_list_requires_admin_session_not_secret_header():
    create_admin()
    create_contributor()
    anonymous = new_client()
    assert anonymous.get("/admin/contributors",
                         headers={"X-Admin-Secret": "anything"}).status_code == 401

    client = new_client()
    admin_login(client)
    response = client.get("/admin/contributors/pending")
    assert response.status_code == 200
    assert response.json()["contributors"] == []
