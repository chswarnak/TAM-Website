
import sqlite3
from pathlib import Path

from main import DB_PATH


def get_event_photos(event_id):
    """
    Retrieve available photographs for one TAM event.
    Only return photos whose files still exist.
    """
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row

        rows = connection.execute(
            """
            SELECT
                id,
                event_id,
                contributor_id,
                filename,
                file_path
            FROM photos
            WHERE event_id = ?
              AND status = 'available'
            ORDER BY id
            """,
            (event_id,)
        ).fetchall()

    photos = []

    for row in rows:
        photo_path = Path(row["file_path"])

        if not photo_path.is_file():
            continue

        photos.append({
            "photo_id": row["id"],
            "event_id": row["event_id"],
            "contributor_id": row["contributor_id"],
            "filename": row["filename"],
            "file_path": str(photo_path)
        })

    return photos
