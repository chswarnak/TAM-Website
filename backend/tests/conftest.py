"""Shared test setup.

Backend modules read configuration (TAM_DB_PATH, SESSION_SECRET, ...) at
import time and load templates/static files relative to the working
directory, so the environment and cwd are fixed here before any backend
module is imported.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DIR = Path(tempfile.mkdtemp(prefix="tam-tests-"))
TEST_DB = TEST_DIR / "test.db"

os.environ["TAM_DB_PATH"] = str(TEST_DB)
os.environ["SESSION_SECRET"] = "test-session-secret-not-for-production"
os.environ["TAM_ALLOWED_ORIGIN"] = "http://testserver"
os.environ["TAM_REQUIRE_HTTPS"] = "0"
os.environ["TAM_UPLOAD_DIR"] = str(TEST_DIR / "uploads")
os.environ.pop("TAM_PRODUCTION", None)
os.environ.pop("TAM_EMAIL_PROVIDER", None)
os.environ.pop("TAM_REKOGNITION_COLLECTION_ID", None)
os.environ.pop("TAM_EVENT_ID", None)

os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import migrations  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    """Give every test an empty, fully migrated database."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{TEST_DB}{suffix}").unlink(missing_ok=True)
    migrations.apply(TEST_DB, backup=False)
    yield TEST_DB
