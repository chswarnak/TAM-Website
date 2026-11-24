"""Location of the SQLite database.

The schema is created and upgraded by migrate.py (see database/SETUP.md).
"""

import os
from pathlib import Path

DB_PATH = Path(
    os.environ.get(
        "TAM_DB_PATH",
        str(Path(__file__).parent / "smart_event.db")
    )
)
