import re
from pathlib import Path

import pytest

from settings import ConfigError, load_settings

BACKEND = Path(__file__).resolve().parent.parent


def test_collection_id_default_is_the_live_collection():
    # The live server's Rekognition collection must keep being used.
    assert load_settings({}).collection_id == "tam-diwali-2026-faces"


def test_collection_id_default_does_not_follow_event_id():
    settings = load_settings({"TAM_EVENT_ID": "tam-ugadi-2027"})
    assert settings.event_id == "tam-ugadi-2027"
    assert settings.collection_id == "tam-diwali-2026-faces"
    assert settings.collection_id_explicit is False


def test_explicit_collection_id_is_flagged():
    settings = load_settings({"TAM_REKOGNITION_COLLECTION_ID": "test-faces"})
    assert settings.collection_id == "test-faces"
    assert settings.collection_id_explicit is True


def test_production_defaults():
    settings = load_settings({})
    assert settings.event_id == "tam-diwali-2026"
    assert settings.allowed_origin == "https://tam-smartphotos.duckdns.org"
    assert settings.require_https is True


@pytest.mark.parametrize("env", [
    {"TAM_PRODUCTION": "1", "TAM_ALLOWED_ORIGIN": "http://127.0.0.1:8000"},
    {"TAM_PRODUCTION": "1", "TAM_REQUIRE_HTTPS": "0"},
])
def test_production_refuses_weakened_origin_checks(env):
    with pytest.raises(ConfigError):
        load_settings(env)


def test_local_testing_can_relax_origin():
    settings = load_settings({
        "TAM_ALLOWED_ORIGIN": "http://127.0.0.1:8000",
        "TAM_REQUIRE_HTTPS": "0",
    })
    assert settings.allowed_origin == "http://127.0.0.1:8000"
    assert settings.require_https is False


def test_rate_limit_defaults_and_overrides():
    defaults = load_settings({})
    assert defaults.login_max_failures_per_identifier == 5
    assert defaults.login_max_failures_per_ip >= 300
    custom = load_settings({"TAM_LOGIN_MAX_FAILURES_PER_IP": "1000"})
    assert custom.login_max_failures_per_ip == 1000


def test_invalid_integer_setting_fails_fast():
    with pytest.raises(ConfigError):
        load_settings({"TAM_LOGIN_WINDOW_MINUTES": "soon"})


def test_no_hard_coded_event_or_collection_ids_in_code():
    offenders = []
    for path in BACKEND.glob("*.py"):
        if path.name == "settings.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"tam-diwali-2026|duckdns", line):
                offenders.append(f"{path.name}:{number}")
    for path in (BACKEND / "templates").glob("*.html"):
        if "tam-diwali-2026" in path.read_text():
            offenders.append(path.name)
    assert offenders == []
