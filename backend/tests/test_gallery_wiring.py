from fastapi.testclient import TestClient

import main


def test_admin_gallery_routes_are_registered():
    client = TestClient(main.app)
    # 404 would mean the router is not mounted; auth errors mean it is.
    response = client.get("/admin/gallery/status")
    assert response.status_code in (401, 403)


def test_gallery_page_does_not_crash_without_login():
    client = TestClient(main.app)
    response = client.get("/gallery", follow_redirects=False)
    assert response.status_code in (303, 401, 403)
