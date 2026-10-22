"""SQLite connection handling for the Student 5 database microservice.

Staff & Shift Management — database layer only. This module owns how the
service connects to its own SQLite database; it contains no API routes and no
feature business logic.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, Union

DATABASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = DATABASE_DIR / "schema.sql"

#: Default on-disk location of this microservice's database.
DEFAULT_DATABASE_PATH = DATABASE_DIR / "staff_shift.db"

#: Environment variable used to relocate the database (e.g. inside Docker).
DATABASE_PATH_ENV_VAR = "STUDENT5_DATABASE_PATH"

# Azure Files is mounted over SMB in Container Apps. SQLite's POSIX byte-range
# locking cannot complete the rollback-journal commit protocol on that mount,
# even when there is only one database replica. The cloud deployment therefore
# opts into SQLite's documented ``nolock`` URI mode and this module serialises
# every database connection inside the single database process. Local and
# Docker development keep SQLite's normal file locking.
SQLITE_NOLOCK_ENV_VAR = "STUDENT5_SQLITE_NOLOCK"
_NOLOCK_CONNECTION_LOCK = threading.RLock()

# Azure Container Apps can briefly overlap old and new revisions during an
# update. Both revisions see the same Azure Files database, so a short-lived
# SQLite lock must be allowed to clear instead of failing at the sqlite3
# module's five-second default. WAL is deliberately not enabled because it is
# not safe for a database accessed through a network filesystem.
SQLITE_BUSY_TIMEOUT_MS = 30_000

REQUIRED_SCHEMA_OBJECTS = frozenset({
    ("table", "staff"),
    ("table", "shift"),
    ("table", "shift_assignment"),
    ("table", "staff_weekly_availability"),
    ("table", "staff_unavailability_request"),
    ("index", "idx_staff_department"),
    ("index", "idx_staff_role"),
    ("index", "idx_staff_availability"),
    ("index", "idx_shift_date"),
    ("index", "idx_shift_department"),
    ("index", "idx_shift_status"),
    ("index", "idx_assignment_shift"),
    ("index", "idx_assignment_staff"),
    ("index", "idx_assignment_status"),
    ("index", "idx_weekly_availability_staff"),
    ("index", "idx_unavailability_staff"),
    ("index", "idx_unavailability_status"),
    ("trigger", "trg_staff_updated_at"),
    ("trigger", "trg_shift_updated_at"),
    ("trigger", "trg_shift_assignment_updated_at"),
    ("trigger", "trg_weekly_availability_updated_at"),
    ("trigger", "trg_unavailability_request_updated_at"),
})

PathLike = Union[str, Path]


def sqlite_nolock_enabled() -> bool:
    """Return whether single-process Azure Files compatibility is enabled."""
    return os.environ.get(SQLITE_NOLOCK_ENV_VAR, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


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

    connection_target = str(target)
    connect_options: dict[str, object] = {}
    if sqlite_nolock_enabled() and connection_target != ":memory:":
        if not target.is_absolute():
            raise RuntimeError(
                f"{SQLITE_NOLOCK_ENV_VAR} requires an absolute database path."
            )
        connection_target = f"{target.as_uri()}?nolock=1"
        connect_options["uri"] = True

    connection = sqlite3.connect(
        connection_target,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
        **connect_options,
    )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS};")
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


@contextmanager
def get_connection(database_path: Optional[PathLike] = None) -> Iterator[sqlite3.Connection]:
    """Context manager that commits on success and rolls back on error."""
    lock = _NOLOCK_CONNECTION_LOCK if sqlite_nolock_enabled() else None
    if lock is not None:
        lock.acquire()
    connection = None
    try:
        connection = connect(database_path)
        yield connection
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()
        if lock is not None:
            lock.release()


def apply_schema(connection: sqlite3.Connection) -> None:
    """Create the tables, indexes, and triggers defined in ``schema.sql``."""
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_is_current(connection: sqlite3.Connection) -> bool:
    """Return whether every required table, index, and trigger exists.

    An established persistent database does not need to rerun the DDL script
    on every container revision. Avoiding that unnecessary write-lock request
    also lets the old revision continue serving while Azure activates the new
    one. A missing object still takes the normal idempotent schema path.
    """
    rows = connection.execute(
        """
        SELECT type, name
        FROM sqlite_master
        WHERE type IN ('table', 'index', 'trigger');
        """
    ).fetchall()
    existing = {(row["type"], row["name"]) for row in rows}
    return REQUIRED_SCHEMA_OBJECTS.issubset(existing)


def drop_all(connection: sqlite3.Connection) -> None:
    """Drop every table owned by this microservice.

    Used by ``init_db.py --reset`` to rebuild a clean database.
    ``shift_assignment`` is dropped first because it holds the foreign keys.
    """
    for table in ("staff_unavailability_request", "staff_weekly_availability",
                  "shift_assignment", "shift", "staff"):
        connection.execute(f"DROP TABLE IF EXISTS {table};")
