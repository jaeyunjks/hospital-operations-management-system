"""SQLite connection and schema handling for Student 3's pharmacy database."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

DATABASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = DATABASE_DIR / "schema.sql"
SEED_DATA_PATH = DATABASE_DIR / "seed_data.sql"
DEFAULT_DATABASE_PATH = DATABASE_DIR / "pharmacy.db"
DATABASE_PATH_ENV_VAR = "STUDENT3_DATABASE_PATH"

PathLike = Union[str, Path]


def get_database_path() -> Path:
    """Return the configured database file path."""
    configured = os.environ.get(DATABASE_PATH_ENV_VAR)
    return Path(configured) if configured else DEFAULT_DATABASE_PATH


def connect(database_path: Optional[PathLike] = None) -> sqlite3.Connection:
    """Open a SQLite connection with foreign-key support and named rows."""
    target = Path(database_path) if database_path is not None else get_database_path()
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


@contextmanager
def get_connection(database_path: Optional[PathLike] = None) -> Iterator[sqlite3.Connection]:
    """Commit on success and roll back if an operation fails."""
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
    """Create Student 3's tables and triggers from ``schema.sql``."""
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def apply_seed_data(connection: sqlite3.Connection) -> None:
    """Load the sample pharmacy and staff records from ``seed_data.sql``."""
    connection.executescript(SEED_DATA_PATH.read_text(encoding="utf-8"))


def is_empty(connection: sqlite3.Connection) -> bool:
    """Return whether the primary seed table has no records."""
    return connection.execute("SELECT COUNT(*) FROM medicines;").fetchone()[0] == 0
