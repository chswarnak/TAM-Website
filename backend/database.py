
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(
    os.environ.get(
        "TAM_DB_PATH",
        str(Path(__file__).parent / "smart_event.db")
    )
)

def create_database():
    connection = sqlite3.connect(DB_PATH)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            approved INTEGER DEFAULT 0
        )
    """)

    # Store individual TAM organizer accounts
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organizer_permissions (
            admin_id INTEGER PRIMARY KEY,
            can_approve_contributors INTEGER NOT NULL DEFAULT 0,
            can_manage_photos INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (admin_id) REFERENCES admins(id)
        )
    """)


    connection.commit()
    connection.close()

if __name__ == "__main__":
    create_database()
    print("Contributor database created successfully!")
