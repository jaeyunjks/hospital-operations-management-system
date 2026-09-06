"""
init_db.py - Database bootstrap for the Clinical Staff Management microservice.

Behaviour:
  * The SQLite file path is taken from the DATABASE_PATH environment variable,
    defaulting to "clinical_staff.db" in the current working directory. This is
    the same rule database/app.py uses, so the two always agree.
  * On a normal run it creates the schema from schema.sql ONLY when the database
    is missing or has no tables yet. A fresh table creation is immediately
    followed by loading seed_data.sql.
  * Seed data is never applied to a database that already has tables/rows -
    only right after this script builds the schema from empty.
  * Passing --reset (or calling init_db(reset=True)) is the ONLY way to destroy
    data: it drops all five tables and rebuilds + reseeds from scratch.
  * Importable: `from init_db import init_db; init_db()` - no subprocess needed.
  * Runnable: `python init_db.py [--reset]`.
"""

import argparse
import os
import sqlite3

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

    conn = sqlite3.connect(db_path)
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
            # never drop without --reset. Leave it untouched.
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
