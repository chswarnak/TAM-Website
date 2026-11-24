import sqlite3
from pathlib import Path

import migrations

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_SQL = REPO_ROOT / "database" / "migrations" / "000_baseline.sql"
SCHEMA_SQL = REPO_ROOT / "database" / "schema.sql"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def test_fresh_database_has_gallery_schema(fresh_db):
    with sqlite3.connect(fresh_db) as conn:
        assert "gallery_enabled" in _columns(conn, "events")
        assert "gallery_approved" in _columns(conn, "photos")
        assert "gallery_downloads" in _tables(conn)


def test_migrations_are_idempotent(fresh_db):
    migrations.apply(fresh_db, backup=False)
    migrations.apply(fresh_db, backup=False)
    with sqlite3.connect(fresh_db) as conn:
        versions = [r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )]
    assert versions == sorted(set(versions))
    assert versions[-1] == migrations.MIGRATIONS[-1][0]


def test_legacy_database_keeps_data_and_gets_backup(tmp_path):
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as conn:
        conn.executescript(BASELINE_SQL.read_text())
        conn.execute(
            "INSERT INTO contributors (name, event_id, approved, password_hash)"
            " VALUES ('Old Timer', 'tam-diwali-2026', 1, 'hash')"
        )

    backup_path = migrations.apply(legacy, backup=True)

    assert backup_path is not None and backup_path.exists()
    with sqlite3.connect(backup_path) as conn:
        assert "schema_migrations" not in _tables(conn)
    with sqlite3.connect(legacy) as conn:
        row = conn.execute(
            "SELECT name, approved, password_hash FROM contributors"
        ).fetchone()
        assert row == ("Old Timer", 1, "hash")
        assert "email" in _columns(conn, "contributors")


def test_hand_patched_live_database_migrates(tmp_path):
    """The live DB may already have gallery columns added by hand."""
    live = tmp_path / "live.db"
    with sqlite3.connect(live) as conn:
        conn.executescript(BASELINE_SQL.read_text())
        conn.execute(
            "ALTER TABLE events ADD COLUMN gallery_enabled "
            "INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "ALTER TABLE photos ADD COLUMN gallery_approved "
            "INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "CREATE TABLE gallery_downloads (id INTEGER PRIMARY KEY, "
            "event_id TEXT, media_type TEXT, media_id INTEGER, "
            "downloader_type TEXT, downloader_id TEXT, downloaded_at_utc TEXT)"
        )

    migrations.apply(live, backup=False)

    with sqlite3.connect(live) as conn:
        assert "attendees" in _tables(conn)


def test_schema_sql_matches_migrations(fresh_db):
    assert SCHEMA_SQL.read_text() == migrations.dump_schema(fresh_db)
