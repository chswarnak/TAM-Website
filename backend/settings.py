"""Application configuration, read once from environment variables.

Defaults are the live production values, so an unconfigured server behaves
exactly as before. Local testing relaxes the origin/HTTPS checks through
TAM_ALLOWED_ORIGIN / TAM_REQUIRE_HTTPS; TAM_PRODUCTION=1 refuses to start
with relaxed values so production can never be weakened by accident.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EVENT_ID = "tam-diwali-2026"
# The live Rekognition collection. Deliberately NOT derived from the event
# id, so changing TAM_EVENT_ID never silently switches collections.
DEFAULT_COLLECTION_ID = "tam-diwali-2026-faces"
DEFAULT_ALLOWED_ORIGIN = "https://tam-smartphotos.duckdns.org"


class ConfigError(RuntimeError):
    """Raised when the environment contains an invalid or unsafe setting."""


@dataclass(frozen=True)
class Settings:
    event_id: str
    collection_id: str
    collection_id_explicit: bool
    allowed_origin: str
    require_https: bool
    production: bool
    public_base_url: str
    upload_dir: Path
    log_level: str

    admin_notify_email: str
    email_provider: str
    email_from: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    notify_interval_minutes: int

    login_max_failures_per_identifier: int
    login_max_failures_per_ip: int
    login_window_minutes: int

    @property
    def email_enabled(self) -> bool:
        return bool(self.email_provider and self.email_from)


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from error
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    raise ConfigError(f"{name} must be 1 or 0, got {raw!r}")


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    explicit_collection = env.get("TAM_REKOGNITION_COLLECTION_ID", "").strip()
    allowed_origin = (
        env.get("TAM_ALLOWED_ORIGIN", "").strip().rstrip("/")
        or DEFAULT_ALLOWED_ORIGIN
    )
    require_https = _flag(env, "TAM_REQUIRE_HTTPS", True)
    production = env.get("TAM_PRODUCTION") == "1"

    if production and not allowed_origin.startswith("https://"):
        raise ConfigError("TAM_PRODUCTION=1 requires an https:// TAM_ALLOWED_ORIGIN")
    if production and not require_https:
        raise ConfigError("TAM_PRODUCTION=1 cannot run with TAM_REQUIRE_HTTPS=0")

    email_provider = env.get("TAM_EMAIL_PROVIDER", "").strip().lower()
    if email_provider not in ("", "ses", "smtp"):
        raise ConfigError("TAM_EMAIL_PROVIDER must be 'ses', 'smtp' or unset")

    backend_dir = Path(__file__).resolve().parent

    return Settings(
        event_id=env.get("TAM_EVENT_ID", "").strip() or DEFAULT_EVENT_ID,
        collection_id=explicit_collection or DEFAULT_COLLECTION_ID,
        collection_id_explicit=bool(explicit_collection),
        allowed_origin=allowed_origin,
        require_https=require_https,
        production=production,
        public_base_url=(
            env.get("TAM_PUBLIC_BASE_URL", "").strip().rstrip("/")
            or allowed_origin
        ),
        upload_dir=Path(env.get("TAM_UPLOAD_DIR", "") or backend_dir / "uploads"),
        log_level=env.get("TAM_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        admin_notify_email=env.get("TAM_ADMIN_NOTIFY_EMAIL", "").strip(),
        email_provider=email_provider,
        email_from=env.get("TAM_EMAIL_FROM", "").strip(),
        smtp_host=env.get("TAM_SMTP_HOST", "").strip(),
        smtp_port=_int(env, "TAM_SMTP_PORT", 587),
        smtp_username=env.get("TAM_SMTP_USERNAME", "").strip(),
        smtp_password=env.get("TAM_SMTP_PASSWORD", ""),
        notify_interval_minutes=_int(env, "TAM_NOTIFY_INTERVAL_MINUTES", 5),
        login_max_failures_per_identifier=_int(
            env, "TAM_LOGIN_MAX_FAILURES_PER_IDENTIFIER", 5
        ),
        login_max_failures_per_ip=_int(env, "TAM_LOGIN_MAX_FAILURES_PER_IP", 500),
        login_window_minutes=_int(env, "TAM_LOGIN_WINDOW_MINUTES", 15),
    )


SETTINGS = load_settings()
