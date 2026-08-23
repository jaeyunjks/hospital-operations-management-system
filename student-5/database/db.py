"""SQLite connection handling for the Student 5 database microservice.

Staff & Shift Management — database layer only. This module owns how the
service connects to its own SQLite database; it contains no API routes and no
feature business logic.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

DATABASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = DATABASE_DIR / "schema.sql"

#: Default on-disk location of this microservice's database.
DEFAULT_DATABASE_PATH = DATABASE_DIR / "staff_shift.db"

#: Environment variable used to relocate the database (e.g. inside Docker).
DATABASE_PATH_ENV_VAR = "STUDENT5_DATABASE_PATH"

PathLike = Union[str, Path]


def get_database_path() -> Path:
    """Return the configured database path.

    Falls back to ``DEFAULT_DATABASE_PATH`` when the environment variable is
    not set, so the service works out of the box for local development.
    """
    configured = os.environ.get(DATABASE_PATH_ENV_VAR)
    return Path(configured) if configured else DEFAULT_DATABASE_PATH


def connect(database_path: Optional[PathLike] = None) -> sqlite3.Connection:
    """Open a connection to the Staff & Shift Management database.

    Rows are returned as ``sqlite3.Row`` so callers can access columns by name,
    and foreign key enforcement is enabled (SQLite disables it per connection
    by default, which would otherwise silently ignore the relationships).
    """
    target = Path(database_path) if database_path is not None else get_database_path()

    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


@contextmanager
def get_connection(database_path: Optional[PathLike] = None) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success and rolls back on error."""
    connection = connect(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def apply_schema(connection: sqlite3.Connection) -> None:
    """Create the tables, indexes, and triggers defined in ``schema.sql``."""
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def drop_all(connection: sqlite3.Connection) -> None:
    """Drop every table owned by this microservice.

    Used by ``init_db.py --reset`` to rebuild a clean database.
    ``shift_assignment`` is dropped first because it holds the foreign keys.
    """
    for table in ("shift_assignment", "shift", "staff"):
        connection.execute(f"DROP TABLE IF EXISTS {table};")
