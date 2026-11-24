import logging
import re
from pathlib import Path

from botocore.exceptions import EndpointConnectionError

import main
from client_helpers import account_login, new_client
from fakes import make_photo
from helpers import create_contributor, create_event

BACKEND = Path(__file__).resolve().parent.parent
CLI_SCRIPTS = {"create_super_admin.py"}


def _app_modules():
    return [p for p in BACKEND.glob("*.py") if p.name not in CLI_SCRIPTS]


def test_no_print_calls_in_app_code():
    offenders = [
        f"{p.name}:{n}"
        for p in _app_modules()
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"^\s*print\(", line)
    ]
    assert offenders == []


def test_no_broad_exception_handlers_in_app_code():
    offenders = [
        f"{p.name}:{n}"
        for p in _app_modules()
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if re.search(r"except\s+(Exception|BaseException)?\s*(as \w+)?:", line)
    ]
    assert offenders == []


def test_failed_upload_is_logged_with_cause(monkeypatch, caplog):
    create_event()
    create_contributor(email="asha@example.com")

    def broken_upload(*_args):
        raise EndpointConnectionError(endpoint_url="https://s3.example")

    monkeypatch.setattr(main, "upload_photo_to_s3", broken_upload)
    client = new_client()
    assert account_login(client, "contributor", "asha@example.com",
                         "contrib-pass-1").status_code == 200

    with caplog.at_level(logging.ERROR):
        response = client.post("/upload", files={
            "file": ("p.jpg", make_photo((255, 0, 0)), "image/jpeg")})

    assert response.status_code == 503
    assert any(r.exc_info and "EndpointConnectionError" in r.exc_text
               for r in caplog.records if r.exc_text)
