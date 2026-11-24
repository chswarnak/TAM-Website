import dataclasses

import pytest

import rate_limit
from client_helpers import account_login, new_client
from helpers import create_admin, create_contributor, db


def _fail(client, identifier="asha@example.com", times=1):
    for _ in range(times):
        response = account_login(client, "contributor", identifier, "wrong-password")
    return response


def test_identifier_is_locked_after_five_failures():
    create_contributor()
    client = new_client()
    assert _fail(client, times=5).status_code == 401

    blocked = account_login(client, "contributor", "asha@example.com", "contrib-pass-1")

    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_other_identifiers_from_same_ip_are_not_blocked():
    create_contributor()
    client = new_client()
    _fail(client, identifier="someone@example.com", times=5)
    assert account_login(client, "contributor", "asha@example.com",
                         "contrib-pass-1").status_code == 200


def test_success_clears_identifier_failures():
    create_contributor()
    client = new_client()
    _fail(client, times=4)
    assert account_login(client, "contributor", "asha@example.com",
                         "contrib-pass-1").status_code == 200
    assert _fail(client, times=4).status_code == 401


def test_per_ip_limit_is_configurable(monkeypatch):
    monkeypatch.setattr(rate_limit, "SETTINGS", dataclasses.replace(
        rate_limit.SETTINGS, login_max_failures_per_ip=3))
    client = new_client()
    for n in range(3):
        _fail(client, identifier=f"user{n}@example.com")
    assert _fail(client, identifier="fresh@example.com").status_code == 429


def test_spoofed_real_ip_header_is_ignored_from_untrusted_peer(monkeypatch):
    monkeypatch.setattr(rate_limit, "SETTINGS", dataclasses.replace(
        rate_limit.SETTINGS, login_max_failures_per_ip=3))
    client = new_client()
    for n in range(3):
        client.post("/contributor/login",
                    json={"identifier": f"u{n}@example.com", "password": "x"},
                    headers={"X-Real-IP": f"10.0.0.{n}"})
    response = client.post("/contributor/login",
                           json={"identifier": "z@example.com", "password": "x"},
                           headers={"X-Real-IP": "10.0.0.99"})
    assert response.status_code == 429


def test_admin_login_is_rate_limited():
    create_admin()
    client = new_client()
    for _ in range(5):
        client.post("/admin/login", data={"username": "boss", "password": "nope"})
    response = client.post("/admin/login", data={"username": "boss",
                                                 "password": "super-secret-pass"})
    assert response.status_code == 429


def test_identifiers_are_not_stored_in_clear():
    client = new_client()
    _fail(client, identifier="private@example.com")
    with db() as conn:
        rows = [tuple(r) for r in conn.execute("SELECT scope, key_hash FROM login_attempts")]
    assert rows
    assert all("private" not in key for _, key in rows)


@pytest.mark.parametrize("peer,header,expected", [
    ("127.0.0.1", "203.0.113.9", "203.0.113.9"),
    ("127.0.0.1", "garbage", "127.0.0.1"),
    ("198.51.100.7", "203.0.113.9", "198.51.100.7"),
])
def test_client_ip_trusts_header_only_from_local_proxy(peer, header, expected):
    from http_security import client_ip

    class Request:
        client = type("C", (), {"host": peer})()
        headers = {"x-real-ip": header}

    assert client_ip(Request()) == expected
