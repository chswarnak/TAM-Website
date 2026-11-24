"""Follow-up items: attendee management, face-indexing thumbnails,
organizer permissions, and keeping already-indexed live photos searchable."""

import sqlite3

import pytest

import attendee_routes
import face_indexing_routes
import migrations
import rekognition_service
from client_helpers import ORIGIN, account_login, admin_login, new_client
from fakes import FakeRekognition, FakeS3, make_photo, make_selfie
from helpers import (create_admin, create_attendee, create_contributor,
                     create_event, create_photo, db)
from settings import SETTINGS

RED = (255, 0, 0)


@pytest.fixture
def world(monkeypatch):
    s3 = FakeS3()
    rekognition = FakeRekognition(s3)
    monkeypatch.setattr(rekognition_service, "get_tam_rekognition_client",
                        lambda: rekognition)
    monkeypatch.setattr(attendee_routes, "get_tam_s3_client", lambda: s3)
    monkeypatch.setattr(face_indexing_routes, "get_tam_s3_client", lambda: s3)
    create_event()
    create_admin()
    contributor = create_contributor()
    key = "tam-diwali-2026/contributor-001/red.jpg"
    s3.objects[("tam-smart-event-photos", key)] = make_photo(RED)
    photo = create_photo(contributor, key)
    return {"s3": s3, "rekognition": rekognition, "photo": photo,
            "contributor": contributor, "key": key}


# ------------------------------------------------- 1. attendee management

def _searching_attendee(world):
    attendee_id = create_attendee()
    rekognition_service.index_photo_now(world["photo"])
    client = new_client()
    account_login(client, "attendee", "ravi@example.com", "attendee-pass-1")
    client.post("/api/attendee-consent", data={"photo_consent": "agree"}, headers=ORIGIN)
    client.post("/api/find-my-photos/search", data={"consent": "yes"}, headers=ORIGIN,
                files={"selfie": ("s.jpg", make_selfie(RED), "image/jpeg")})
    download = f"/api/guest/photos/{world['photo']}/download"
    assert client.get(download).status_code == 200
    return attendee_id, client, download


def _admin_action(path):
    admin = new_client()
    csrf = admin_login(admin)
    return admin.post(path, data={"csrf_token": csrf}, follow_redirects=False)


@pytest.mark.parametrize("action", ["disable", "delete"])
def test_disabled_or_deleted_attendee_loses_access_immediately(world, action):
    attendee_id, client, download = _searching_attendee(world)

    assert _admin_action(f"/admin/accounts/attendee/{attendee_id}/{action}").status_code == 303

    assert client.get(download).status_code == 401
    assert client.get("/api/guest/photos").status_code == 401
    assert account_login(client, "attendee", "ravi@example.com",
                         "attendee-pass-1").status_code == 403
    with db() as conn:  # stored search results are revoked too
        assert conn.execute("SELECT COUNT(*) FROM guest_search_sessions "
                            "WHERE attendee_id = ?", (attendee_id,)).fetchone()[0] == 0


def test_reenabled_attendee_can_log_in_again(world):
    attendee_id = create_attendee()
    _admin_action(f"/admin/accounts/attendee/{attendee_id}/disable")
    _admin_action(f"/admin/accounts/attendee/{attendee_id}/enable")
    assert account_login(new_client(), "attendee", "ravi@example.com",
                         "attendee-pass-1").status_code == 200


def test_deleted_attendee_cannot_be_reenabled(world):
    attendee_id = create_attendee()
    _admin_action(f"/admin/accounts/attendee/{attendee_id}/delete")
    assert _admin_action(
        f"/admin/accounts/attendee/{attendee_id}/enable").status_code == 404


def test_attendee_management_requires_permission(world):
    attendee_id = create_attendee()
    create_admin(username="helper", password="helper-password-1", role="admin")
    admin = new_client()
    csrf = admin_login(admin, "helper", "helper-password-1")
    response = admin.post(f"/admin/accounts/attendee/{attendee_id}/disable",
                          data={"csrf_token": csrf})
    assert response.status_code == 403


# --------------------------------------------- 2. face-indexing thumbnails

def test_photo_manager_sees_face_indexing_thumbnails(world):
    create_admin(username="photos", password="photos-password-1", role="admin",
                 can_manage_photos=True)
    admin = new_client()
    admin_login(admin, "photos", "photos-password-1")

    page = admin.get("/admin/photos/face-indexing")
    thumb = admin.get(f"/admin/photos/face-indexing/{world['photo']}/thumbnail")

    assert f"/admin/photos/face-indexing/{world['photo']}/thumbnail" in page.text
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"


def test_thumbnail_requires_photo_permission(world):
    create_admin(username="helper", password="helper-password-1", role="admin",
                 can_approve=True)
    admin = new_client()
    admin_login(admin, "helper", "helper-password-1")
    assert admin.get(
        f"/admin/photos/face-indexing/{world['photo']}/thumbnail").status_code == 403


# ------------------------------------------- 3. organizer permission screen

def _permissions(admin_id):
    with db() as conn:
        row = conn.execute("SELECT can_approve_contributors, can_manage_photos "
                           "FROM organizer_permissions WHERE admin_id = ?",
                           (admin_id,)).fetchone()
    return tuple(row) if row else None


def test_super_admin_grants_and_revokes_permissions():
    create_admin()
    organizer = create_admin(username="helper", password="helper-password-1",
                             role="admin")
    admin = new_client()
    csrf = admin_login(admin)
    assert "can_manage_photos" in admin.get("/admin/organizers").text

    admin.post(f"/admin/organizers/{organizer}/permissions", data={
        "csrf_token": csrf, "can_approve_contributors": "1", "can_manage_photos": "1"})
    assert _permissions(organizer) == (1, 1)

    admin.post(f"/admin/organizers/{organizer}/permissions", data={
        "csrf_token": csrf, "can_manage_photos": "1"})
    assert _permissions(organizer) == (0, 1)


def test_only_super_admin_can_change_permissions():
    create_admin(username="helper", password="helper-password-1", role="admin",
                 can_approve=True, can_manage_photos=True)
    other = create_admin(username="other", password="other-password-1", role="admin")
    admin = new_client()
    csrf = admin_login(admin, "helper", "helper-password-1")
    response = admin.post(f"/admin/organizers/{other}/permissions", data={
        "csrf_token": csrf, "can_manage_photos": "1"})
    assert response.status_code == 403
    assert _permissions(other) is None


def test_super_admin_permissions_cannot_be_edited():
    boss = create_admin()
    admin = new_client()
    csrf = admin_login(admin)
    response = admin.post(f"/admin/organizers/{boss}/permissions",
                          data={"csrf_token": csrf})
    assert response.status_code == 404


# ------------------------------------- 4. already-indexed live photos stay searchable

def _live_style_indexed(world, with_job_status):
    """A photo indexed on the live server before this branch: faces stored,
    authorization present, job in the given state (None = no job row)."""
    face_id = "live-face-1"
    world["rekognition"].face_people[face_id] = RED
    with db() as conn:
        conn.execute("INSERT INTO photo_faces (photo_id, event_id, collection_id, "
                     "rekognition_face_id) VALUES (?, ?, ?, ?)",
                     (world["photo"], SETTINGS.event_id, SETTINGS.collection_id, face_id))
        if with_job_status:
            # A real completed live job already carries its face count.
            conn.execute("INSERT INTO photo_index_jobs (photo_id, collection_id, status, "
                         "face_count, error_message) VALUES (?, ?, ?, 1, 'Indexing "
                         "interrupted; reconciliation required')",
                         (world["photo"], SETTINGS.collection_id, with_job_status))


@pytest.mark.parametrize("job_status", [None, "processing", "completed"])
def test_reconciliation_makes_live_indexed_photos_searchable(world, job_status):
    _live_style_indexed(world, job_status)

    with sqlite3.connect(SETTINGS_DB()) as conn:
        migrations.reconcile_indexed_photos(conn)

    with db() as conn:
        job = conn.execute("SELECT status, face_count FROM photo_index_jobs "
                           "WHERE photo_id = ?", (world["photo"],)).fetchone()
    assert tuple(job) == ("completed", 1)
    matches = rekognition_service.find_matching_event_photos(make_selfie(RED))
    assert [m["photo_id"] for m in matches] == [world["photo"]]


def test_photo_with_faces_is_never_sent_to_rekognition_again(world):
    _live_style_indexed(world, None)
    calls = []
    real_index = world["rekognition"].index_faces
    world["rekognition"].index_faces = lambda **kw: calls.append(kw) or real_index(**kw)

    result = rekognition_service.index_photo_now(world["photo"])

    assert calls == []
    assert result == {"status": "completed", "face_count": 1}


def test_stuck_job_without_faces_becomes_retryable(world):
    with db() as conn:
        conn.execute("INSERT INTO photo_index_jobs (photo_id, collection_id, status) "
                     "VALUES (?, ?, 'processing')", (world["photo"], SETTINGS.collection_id))

    with sqlite3.connect(SETTINGS_DB()) as conn:
        migrations.reconcile_indexed_photos(conn)

    with db() as conn:
        assert conn.execute("SELECT status FROM photo_index_jobs").fetchone()[0] == "failed"


def test_indexing_report_counts_photo_states(world):
    import indexing_report
    _live_style_indexed(world, "completed")
    report = indexing_report.build_report(SETTINGS_DB())
    assert report["searchable"] == 1
    assert report["available_photos"] == 1


def SETTINGS_DB():
    from database import DB_PATH
    return DB_PATH
