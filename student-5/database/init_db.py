#!/usr/bin/env python3
"""Initialise the Student 5 database microservice.

Staff & Shift Management. Creates the schema and optionally loads seed data.

Usage:
    python3 init_db.py                # create schema, seed if empty
    python3 init_db.py --reset        # drop everything, recreate, reseed
    python3 init_db.py --no-seed      # create schema only
    python3 init_db.py --path /tmp/x.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db          # noqa: E402
import seed_data   # noqa: E402


def initialise(database_path=None, reset: bool = False, load_seed: bool = True) -> None:
    target = Path(database_path) if database_path else db.get_database_path()

    with db.get_connection(target) as connection:
        if reset:
            db.drop_all(connection)
            print("Dropped existing tables.")

        db.apply_schema(connection)
        print(f"Schema applied to {target}")

        if not load_seed:
            print("Seeding skipped (--no-seed).")
            return

        if not seed_data.is_empty(connection):
            print("Database already contains data — seeding skipped.")
            print("Use --reset to rebuild and reseed.")
            return

        counts = seed_data.seed(connection)
        print("Seed data inserted:")
        for table, count in counts.items():
            print(f"  {table:<18} {count} records")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialise the Student 5 Staff & Shift Management database."
    )
    parser.add_argument("--path", default=None,
                        help="database file path (default: staff_shift.db beside this script)")
    parser.add_argument("--reset", action="store_true",
                        help="drop existing tables before creating the schema")
    parser.add_argument("--no-seed", dest="seed", action="store_false",
                        help="create the schema without inserting seed data")
    parser.set_defaults(seed=True)
    arguments = parser.parse_args()

    try:
        initialise(arguments.path, reset=arguments.reset, load_seed=arguments.seed)
    except Exception as error:  # surfaced to the caller with a non-zero exit code
        print(f"Initialisation failed: {error}", file=sys.stderr)
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
