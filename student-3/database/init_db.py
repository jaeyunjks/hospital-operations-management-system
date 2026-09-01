#!/usr/bin/env python3
"""Initialise and verify Student 3's pharmacy database.

Usage:
    python3 init_db.py
    python3 init_db.py --check
    python3 init_db.py --path /tmp/pharmacy.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import db

EXPECTED_TABLES = (
    "staff",
    "suppliers",
    "medicines",
    "batches",
    "purchase_orders",
    "stock_movements",
)


def initialise(database_path: str | None = None) -> Path:
    """Apply the schema and seed an empty Student 3 database."""
    target = Path(database_path) if database_path else db.get_database_path()
    with db.get_connection(target) as connection:
        db.apply_schema(connection)
        if db.is_empty(connection):
            db.apply_seed_data(connection)
            print("Seed data inserted.")
        else:
            print("Database already contains data — seeding skipped.")
    print(f"Schema applied to {target}")
    return target


def check(database_path: str | None = None) -> None:
    """Confirm that the expected schema and staff seed-role counts exist."""
    target = Path(database_path) if database_path else db.get_database_path()
    with db.get_connection(target) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table';"
        ).fetchall()
        tables = {row["name"] for row in rows}
        missing = set(EXPECTED_TABLES) - tables
        if missing:
            raise RuntimeError(f"Missing tables: {', '.join(sorted(missing))}")

        role_counts = dict(connection.execute(
            "SELECT role, COUNT(*) FROM staff GROUP BY role;"
        ).fetchall())
        if role_counts.get("manager") != 2 or role_counts.get("staff") != 10:
            raise RuntimeError(
                "Expected 2 manager and 10 staff seed records; "
                f"found {role_counts}."
            )
    print(f"Database check passed for {target}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialise Student 3's pharmacy database."
    )
    parser.add_argument("--path", default=None, help="database file path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="initialise the database, then verify tables and staff seed data",
    )
    arguments = parser.parse_args()

    try:
        initialise(arguments.path)
        if arguments.check:
            check(arguments.path)
    except Exception as error:
        print(f"Initialisation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
