"""Read-only report of face-indexing state, for checking the live database
before and after migrating. Changes nothing.

    python backend/indexing_report.py            # uses TAM_DB_PATH
    python backend/indexing_report.py --db FILE
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from database import DB_PATH
from settings import SETTINGS

_QUERIES = {
    "available_photos": "SELECT COUNT(*) FROM photos p WHERE {base}",
    # Searchable: authorized, job completed, and at least one face stored.
    "searchable": """
        SELECT COUNT(*) FROM photos p WHERE {base} AND {authorized}
          AND EXISTS (SELECT 1 FROM photo_index_jobs j WHERE j.photo_id = p.id
                      AND j.collection_id = :collection AND j.status = 'completed')
          AND EXISTS (SELECT 1 FROM photo_faces f WHERE f.photo_id = p.id
                      AND f.collection_id = :collection)""",
    "indexed_no_faces_found": """
        SELECT COUNT(*) FROM photos p WHERE {base} AND {authorized}
          AND EXISTS (SELECT 1 FROM photo_index_jobs j WHERE j.photo_id = p.id
                      AND j.collection_id = :collection AND j.status = 'completed')
          AND NOT EXISTS (SELECT 1 FROM photo_faces f WHERE f.photo_id = p.id
                          AND f.collection_id = :collection)""",
    "authorized_not_indexed": """
        SELECT COUNT(*) FROM photos p WHERE {base} AND {authorized}
          AND NOT EXISTS (SELECT 1 FROM photo_index_jobs j WHERE j.photo_id = p.id
                          AND j.collection_id = :collection
                          AND j.status IN ('completed', 'failed', 'processing'))""",
    "failed_jobs": """
        SELECT COUNT(*) FROM photo_index_jobs
        WHERE collection_id = :collection AND status = 'failed'""",
    "stuck_processing": """
        SELECT COUNT(*) FROM photo_index_jobs
        WHERE collection_id = :collection AND status = 'processing'""",
    "not_authorized": "SELECT COUNT(*) FROM photos p WHERE {base} AND NOT {authorized}",
    "faces_but_not_authorized": """
        SELECT COUNT(*) FROM photos p WHERE {base} AND NOT {authorized}
          AND EXISTS (SELECT 1 FROM photo_faces f WHERE f.photo_id = p.id
                      AND f.collection_id = :collection)""",
    "faces_in_other_collections": """
        SELECT COUNT(DISTINCT photo_id) FROM photo_faces
        WHERE collection_id != :collection""",
}
_BASE = ("p.event_id = :event AND p.status = 'available' "
         "AND p.s3_bucket = 'tam-smart-event-photos' AND p.s3_key IS NOT NULL")
_AUTHORIZED = ("EXISTS (SELECT 1 FROM photo_face_authorizations a WHERE a.photo_id = p.id "
               "AND a.authorized = 1 AND TRIM(COALESCE(a.consent_reference, '')) != '')")


def build_report(db_path: Path | str) -> dict:
    params = {"event": SETTINGS.event_id, "collection": SETTINGS.collection_id}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        return {
            name: connection.execute(
                sql.format(base=_BASE, authorized=_AUTHORIZED), params).fetchone()[0]
            for name, sql in _QUERIES.items()
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args(argv)
    sys.stdout.write(f"event={SETTINGS.event_id} collection={SETTINGS.collection_id}\n")
    for name, count in build_report(args.db).items():
        sys.stdout.write(f"{name:28} {count}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
