"""The whole journey over HTTP, with fake S3 and Rekognition:
register -> admin approval -> upload -> indexing -> consent -> selfie -> photos."""

import attendee_routes
import main
import rekognition_service
from client_helpers import ORIGIN, account_login, admin_login, new_client
from fakes import FakeRekognition, FakeS3, make_photo, make_selfie
from helpers import create_admin, create_event

RED, BLUE = (255, 0, 0), (0, 0, 255)


def test_selfie_search_end_to_end(monkeypatch, tmp_path):
    s3 = FakeS3()
    rekognition = FakeRekognition(s3)
    monkeypatch.setattr(main, "upload_photo_to_s3", s3.upload_photo_to_s3)
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(attendee_routes, "get_tam_s3_client", lambda: s3)
    monkeypatch.setattr(rekognition_service, "get_tam_rekognition_client",
                        lambda: rekognition)
    create_event(auto_authorize=True)
    create_admin()

    # 1. Both users register; neither can log in yet.
    photographer, attendee = new_client(), new_client()
    photographer.post("/contributor/register", headers=ORIGIN, json={
        "full_name": "Asha Rao", "email": "asha@example.com",
        "mobile_number": "5145550100", "password": "contrib-pass-1"})
    attendee.post("/attendee/register", headers=ORIGIN, json={
        "full_name": "Ravi Kumar", "email": "ravi@example.com",
        "username": "ravi", "password": "attendee-pass-1"})
    assert account_login(attendee, "attendee", "ravi", "attendee-pass-1").status_code == 403

    # 2. The super admin approves everyone pending.
    admin = new_client()
    csrf = admin_login(admin)
    admin.post("/admin/accounts/approve-all", data={"csrf_token": csrf})

    # 3. The contributor uploads; photos are indexed automatically.
    assert account_login(photographer, "contributor", "asha@example.com",
                         "contrib-pass-1").status_code == 200
    uploads = {}
    for name, people in {"ravi_and_friend": (RED, BLUE), "friend_only": (BLUE,)}.items():
        response = photographer.post("/upload", files={
            "file": (f"{name}.jpg", make_photo(*people), "image/jpeg")})
        assert response.status_code == 200, response.text
        uploads[name] = response.json()["photo_id"]

    # 4. The attendee logs in, consents, and searches with a selfie.
    assert account_login(attendee, "attendee", "ravi", "attendee-pass-1").status_code == 200
    assert attendee.post("/api/attendee-consent", headers=ORIGIN,
                         data={"photo_consent": "agree"}).status_code == 200
    search = attendee.post("/api/find-my-photos/search", headers=ORIGIN,
                           data={"consent": "yes"},
                           files={"selfie": ("me.jpg", make_selfie(RED), "image/jpeg")})
    assert search.status_code == 200, search.text
    assert search.json()["match_count"] == 1

    # 5. They see and download exactly the photo they appear in.
    photos = attendee.get("/api/guest/photos").json()["photos"]
    assert [p["photo_id"] for p in photos] == [uploads["ravi_and_friend"]]
    download = attendee.get(f"/api/guest/photos/{uploads['ravi_and_friend']}/download")
    assert download.status_code == 200
    assert download.content[:2] == b"\xff\xd8"  # JPEG
    assert attendee.get(
        f"/api/guest/photos/{uploads['friend_only']}/download").status_code == 403
