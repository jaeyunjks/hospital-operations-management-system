"""
init_db.py - Database bootstrap for the Clinical Staff Management microservice.

Behaviour:
  * The SQLite file path is taken from the DATABASE_PATH environment variable,
    defaulting to "clinical_staff.db" in the current working directory. This is
    the same rule database/app.py uses, so the two always agree.
  * On a normal run it creates the schema from schema.sql ONLY when the database
    is missing or has no tables yet. A fresh table creation is immediately
    followed by loading seed_data.sql.
  * On an existing database it adds any later-release columns that are missing
    (e.g. ai_summaries.source_documents) without touching existing rows.
  * Seed data is never applied to a database that already has tables/rows -
    only right after this script builds the schema from empty.
  * Passing --reset (or calling init_db(reset=True)) is the ONLY way to destroy
    data: it drops all five tables and rebuilds + reseeds from scratch.
  * Importable: `from init_db import init_db; init_db()` - no subprocess needed.
  * Runnable: `python init_db.py [--reset]`.
"""

import argparse
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

# schema.sql / seed_data.sql live next to this file. Resolve relative to the
# module so it works regardless of the caller's working directory.
_HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_FILE = os.path.join(_HERE, "schema.sql")
SEED_FILE = os.path.join(_HERE, "seed_data.sql")

# The five tables owned by this service. Order matters for DROP: children
# (with FKs back to clinical_records) come before the parent.
TABLES = [
    "ai_summaries",
    "surgery_requests",
    "care_tasks",
    "consultation_requests",
    "clinical_records",
]


# Azure Files (SMB) cannot complete SQLite's byte-range locking. The cloud
# deployment sets STUDENT2_SQLITE_NOLOCK, so the file is opened without locks
# (database/app.py then serves one request at a time). Local and Docker runs
# keep SQLite's normal locking.
NOLOCK = os.environ.get("STUDENT2_SQLITE_NOLOCK", "").strip().lower() in {"1", "true", "yes", "on"}


def connect(path):
    """Open the SQLite file, without file locks when NOLOCK is set."""
    if NOLOCK:
        uri = Path(path).resolve().as_uri() + "?nolock=1"
        return sqlite3.connect(uri, uri=True, timeout=30)
    return sqlite3.connect(path, timeout=30)


def _has_all_tables(db_path):
    """True when the file exists and already holds every table this service owns."""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return False
    try:
        conn = connect(db_path)
        try:
            return set(TABLES) <= _existing_tables(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def _build_locally_and_copy(db_path):
    """Build schema + seed on local disk, then copy the finished file into place.

    Building directly on Azure Files can fail half way ("database is locked")
    and leave a file without tables, so the cloud build happens locally.
    """
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, os.path.basename(db_path))
        conn = sqlite3.connect(local)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            _create_schema(conn)
            _load_seed(conn)
            conn.commit()
        finally:
            conn.close()
        shutil.copyfile(local, db_path)


def get_db_path():
    """Resolve the SQLite file path the same way database/app.py does."""
    return os.environ.get("DATABASE_PATH", "clinical_staff.db")


def _read_sql(path):
    """Read a .sql file as text. These files are only ever read, never written."""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _existing_tables(conn):
    """Return the set of this service's tables that already exist in the db."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    present = {r[0] for r in rows}
    return present & set(TABLES)


def _create_schema(conn):
    """Build all tables/indexes from schema.sql."""
    conn.executescript(_read_sql(SCHEMA_FILE))


def _load_seed(conn):
    """Populate the freshly created tables from seed_data.sql."""
    conn.executescript(_read_sql(SEED_FILE))


# Columns added to a table after its first release. schema.sql already has them,
# so this only matters for a database file created before they existed.
# (table, column, definition as it appears in schema.sql)
_ADDED_COLUMNS = [
    ("ai_summaries", "source_documents",
     "TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_documents))"),
]


def _add_missing_columns(conn):
    """Add any _ADDED_COLUMNS an older database lacks. Adds only; never drops or rewrites."""
    for table, column, definition in _ADDED_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if existing and column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            print(f"[init_db] added missing column {table}.{column}")


def _drop_all(conn):
    """Drop every table this service owns (used only by --reset)."""
    for table in TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")


def init_db(reset=False):
    """
    Ensure the database exists and is initialised.

    reset=False (default): create schema + seed ONLY if the database has none
        of this service's tables yet. If tables already exist, nothing is
        touched and existing data is left completely alone.
    reset=True: drop all five tables and rebuild + reseed from scratch. This
        is the only code path that deletes data.

    Returns the resolved database path.
    """
    db_path = get_db_path()

    # Make sure the parent directory exists (e.g. DATABASE_PATH=/data/foo.db).
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if NOLOCK and not reset and not _has_all_tables(db_path):
        _build_locally_and_copy(db_path)
        print(f"[init_db] built schema and seed data locally, then copied to {db_path}")
        return db_path

    conn = connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        if reset:
            # Explicit, opt-in teardown: drop everything, rebuild, reseed.
            _drop_all(conn)
            _create_schema(conn)
            _load_seed(conn)
            conn.commit()
            print(f"[init_db] --reset: dropped and rebuilt all tables at {db_path}")
            return db_path

        present = _existing_tables(conn)

        if not present:
            # Fresh (or empty) database: safe to build and seed.
            _create_schema(conn)
            _load_seed(conn)
            conn.commit()
            print(f"[init_db] created schema and loaded seed data at {db_path}")
        else:
            # Something is already here. Never seed over an existing population;
            # never drop without --reset. The one exception: columns added in
            # later releases are appended (data-preserving) if missing.
            _add_missing_columns(conn)
            conn.commit()
            print(
                f"[init_db] database already initialised at {db_path} "
                f"({len(present)}/{len(TABLES)} tables present); leaving as-is"
            )
        return db_path
    finally:
        conn.close()


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Initialise the Clinical Staff Management SQLite database."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and rebuild all five tables from scratch, then reseed. "
             "This deletes existing data.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    init_db(reset=args.reset)
