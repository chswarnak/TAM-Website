import pytest

import main
import rekognition_service
from client_helpers import account_login, admin_login, new_client
from fakes import FakeRekognition, FakeS3, make_photo
from helpers import create_admin, create_contributor, create_event, db

RED = (255, 0, 0)


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    s3 = FakeS3()
    rekognition = FakeRekognition(s3)
    monkeypatch.setattr(main, "upload_photo_to_s3", s3.upload_photo_to_s3)
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(rekognition_service, "get_tam_rekognition_client",
                        lambda: rekognition)
    return s3, rekognition


def _upload(colour=RED):
    create_contributor()
    client = new_client()
    account_login(client, "contributor", "asha@example.com", "contrib-pass-1")
    response = client.post("/upload", files={
        "file": ("p.jpg", make_photo(colour), "image/jpeg")})
    assert response.status_code == 200, response.text
    return response.json()["photo_id"]


def _job(photo_id):
    with db() as conn:
        return conn.execute(
            "SELECT status, face_count FROM photo_index_jobs WHERE photo_id = ?",
            (photo_id,)).fetchone()


def _authorization(photo_id):
    with db() as conn:
        return conn.execute(
            "SELECT authorized, consent_reference FROM photo_face_authorizations "
            "WHERE photo_id = ?", (photo_id,)).fetchone()


def test_upload_is_indexed_when_event_switch_is_on(fakes):
    create_event(auto_authorize=True)
    photo_id = _upload()

    auth = _authorization(photo_id)
    assert auth["authorized"] == 1
    assert auth["consent_reference"].startswith("event-policy:")
    assert tuple(_job(photo_id)) == ("completed", 1)


def test_upload_waits_for_admin_when_switch_is_off(fakes):
    create_event(auto_authorize=False)
    photo_id = _upload()

    assert _authorization(photo_id) is None
    assert _job(photo_id) is None


def test_admin_authorizes_pending_photos(fakes):
    create_event(auto_authorize=False)
    create_admin()
    photo_id = _upload()
    admin = new_client()
    csrf = admin_login(admin)

    page = admin.get("/admin/photos/face-indexing")
    assert page.status_code == 200
    assert f"photo-{photo_id}" in page.text

    response = admin.post("/admin/photos/face-indexing/authorize", data={
        "csrf_token": csrf, "photo_ids": [str(photo_id)]}, follow_redirects=False)
    assert response.status_code == 303
    assert _authorization(photo_id)["consent_reference"].startswith("admin:")
    assert tuple(_job(photo_id)) == ("completed", 1)


def test_admin_authorize_all(fakes):
    create_event(auto_authorize=False)
    create_admin()
    first = _upload()
    with db() as conn:  # a second photo from the same contributor
        conn.execute("UPDATE contributors SET email = 'old@example.com'")
    second = _upload()
    admin = new_client()
    csrf = admin_login(admin)

    admin.post("/admin/photos/face-indexing/authorize",
               data={"csrf_token": csrf, "all_pending": "1"})

    assert _job(first)["status"] == _job(second)["status"] == "completed"


def test_face_indexing_page_requires_photo_permission(fakes):
    create_event()
    create_admin(username="helper", password="helper-password-1", role="admin",
                 can_approve=True)
    admin = new_client()
    admin_login(admin, "helper", "helper-password-1")
    assert admin.get("/admin/photos/face-indexing").status_code == 403


def test_indexing_failure_does_not_fail_the_upload(fakes):
    _, rekognition = fakes
    rekognition.collections.clear()
    create_event(auto_authorize=True)

    photo_id = _upload()

    assert _job(photo_id)["status"] == "failed"


def test_events_page_toggles_the_switch():
    create_event(auto_authorize=False)
    create_admin()
    admin = new_client()
    csrf = admin_login(admin)
    assert admin.get("/admin/events").status_code == 200
    response = admin.post("/admin/events/save", data={
        "csrf_token": csrf, "event_name": "Test Event", "event_date": "2026-11-23",
        "upload_start_time": "00:00", "upload_end_time": "23:59",
        "status": "active", "retention_hours": "48",
        "auto_authorize_face_indexing": "1"}, follow_redirects=False)
    assert response.status_code == 303
    with db() as conn:
        assert conn.execute("SELECT auto_authorize_face_indexing FROM events"
                            ).fetchone()[0] == 1
