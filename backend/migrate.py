"""Command-line entry point for database migrations.

    python backend/migrate.py                       # TAM_DB_PATH, with backup
    python backend/migrate.py --db local.db         # a specific database
    python backend/migrate.py --dump-schema database/schema.sql
"""

import argparse
import logging
import sys
import tempfile
from pathlib import Path

import migrations
from database import DB_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH,
                        help="SQLite file to migrate (default: TAM_DB_PATH)")
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the pre-migration backup copy")
    parser.add_argument("--dump-schema", type=Path, metavar="FILE",
                        help="write the fully migrated schema to FILE and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.dump_schema:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "schema.db"
            migrations.apply(scratch, backup=False)
            args.dump_schema.write_text(migrations.dump_schema(scratch))
        logging.info("Schema written to %s", args.dump_schema)
        return 0

    backup_path = migrations.apply(args.db, backup=not args.no_backup)
    if backup_path:
        logging.info("Backup kept at %s", backup_path)
    logging.info("Database %s is up to date", args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
