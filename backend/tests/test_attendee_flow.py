import pytest

import attendee_routes
import main
import rekognition_service
from client_helpers import ORIGIN, account_login, new_client
from fakes import FakeRekognition, FakeS3, make_photo, make_selfie
from helpers import (create_attendee, create_contributor, create_event,
                     create_photo, db)

RED, BLUE = (255, 0, 0), (0, 0, 255)
PROTECTED_WORDS = ("photo", "gallery", "find-my", "consent", "search")
PUBLIC_PATHS = {"/gallery/register"}  # now only redirects to login


def _protected_routes():
    for path, methods in main.app.openapi()["paths"].items():
        if path.startswith("/admin") or not any(w in path for w in PROTECTED_WORDS):
            continue
        for method in methods:
            yield method.upper(), path.replace("{photo_id}", "1")


@pytest.mark.parametrize("method,path", list(_protected_routes()))
def test_no_photo_or_search_endpoint_works_without_login(method, path):
    create_event(gallery=True)
    response = new_client().request(method, path, headers=ORIGIN,
                                     follow_redirects=False)
    if response.status_code == 303:
        assert response.headers["location"] in ("/attendee/login",)
    else:
        assert response.status_code in (401, 403, 405, 410, 422), (
            f"{method} {path} returned {response.status_code}")


def test_pending_attendee_cannot_log_in():
    create_attendee(approved=False)
    response = account_login(new_client(), "attendee", "ravi@example.com",
                             "attendee-pass-1")
    assert response.status_code == 403


@pytest.fixture
def world(monkeypatch):
    """An event with indexed photos: RED alone, RED+BLUE, BLUE alone."""
    s3 = FakeS3()
    rekognition = FakeRekognition(s3)
    monkeypatch.setattr(rekognition_service, "get_tam_rekognition_client",
                        lambda: rekognition)
    monkeypatch.setattr(attendee_routes, "get_tam_s3_client", lambda: s3)
    create_event()
    contributor = create_contributor()
    photos = {}
    for name, people in {"red": (RED,), "both": (RED, BLUE), "blue": (BLUE,)}.items():
        key = f"tam-diwali-2026/contributor-001/{name}.jpg"
        s3.objects[("tam-smart-event-photos", key)] = make_photo(*people)
        photos[name] = create_photo(contributor, key)
        rekognition_service.index_photo_now(photos[name])
    return photos


def _attendee(email="ravi@example.com"):
    create_attendee(email=email)
    client = new_client()
    assert account_login(client, "attendee", email, "attendee-pass-1").status_code == 200
    return client


def _consent(client, choice="agree"):
    return client.post("/api/attendee-consent", data={"photo_consent": choice},
                       headers=ORIGIN)


def _search(client, colour):
    return client.post("/api/find-my-photos/search",
                       files={"selfie": ("s.jpg", make_selfie(colour), "image/jpeg")},
                       data={"consent": "yes"}, headers=ORIGIN)


def test_consent_is_recorded_against_the_account(world):
    client = _attendee()
    assert client.get("/api/find-my-photos/consent-status").json() == {
        "consent_verified": False}

    assert _consent(client).json()["status"] == "recorded"

    assert client.get("/api/find-my-photos/consent-status").json() == {
        "consent_verified": True}
    with db() as conn:
        rows = conn.execute("SELECT consent_type, attendee_id, attendee_name "
                            "FROM attendee_consents ORDER BY consent_type").fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("facial_indexing", 1, "Ravi Kumar"), ("selfie_search", 1, "Ravi Kumar")]


def test_search_requires_consent(world):
    client = _attendee()
    assert _search(client, RED).status_code == 403


def test_search_returns_only_my_photos(world):
    client = _attendee()
    _consent(client)

    response = _search(client, RED)

    assert response.status_code == 200, response.text
    assert response.json()["match_count"] == 2
    listed = {p["photo_id"] for p in client.get("/api/guest/photos").json()["photos"]}
    assert listed == {world["red"], world["both"]}
    assert client.get(f"/api/guest/photos/{world['red']}/thumbnail").status_code == 200
    download = client.get(f"/api/guest/photos/{world['both']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/guest/photos/{world['blue']}/download").status_code == 403


def test_another_attendee_cannot_use_my_results(world):
    mine = _attendee()
    _consent(mine)
    _search(mine, RED)

    other = _attendee(email="other@example.com")
    _consent(other)
    _search(other, BLUE)

    assert other.get(f"/api/guest/photos/{world['red']}/download").status_code == 403


def test_declining_consent_revokes_access(world):
    client = _attendee()
    _consent(client)
    _search(client, RED)

    assert _consent(client, "decline").json()["status"] == "declined"

    assert client.get(f"/api/guest/photos/{world['red']}/download").status_code in (401, 403)
    assert client.get("/api/find-my-photos/consent-status").json() == {
        "consent_verified": False}


def test_logout_ends_photo_access(world):
    client = _attendee()
    _consent(client)
    _search(client, RED)
    client.post("/attendee/logout")
    assert client.get(f"/api/guest/photos/{world['red']}/download").status_code == 401


def test_search_rejects_foreign_origin(world):
    client = _attendee()
    _consent(client)
    response = client.post(
        "/api/find-my-photos/search",
        files={"selfie": ("s.jpg", make_selfie(RED), "image/jpeg")},
        data={"consent": "yes"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_logged_in_attendee_sees_gallery():
    create_event(gallery=True)
    client = _attendee()
    assert client.get("/gallery", follow_redirects=False).status_code == 200


def test_anonymous_gallery_registration_is_gone():
    create_event(gallery=True)
    client = new_client()
    page = client.get("/gallery/register", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/attendee/login"
    assert client.post("/api/gallery/guest/register", headers=ORIGIN).status_code == 410
