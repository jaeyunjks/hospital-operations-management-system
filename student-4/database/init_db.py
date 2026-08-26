"""
Create and populate the Room, Bed & Operating Theatre database.

Usage
-----
    python init_db.py              # create rooms.db in this directory
    python init_db.py --check      # create, then run consistency checks
    python init_db.py --db /path/to/rooms.db

The script is safe to re-run: schema.sql drops every table before
recreating it, so the database is rebuilt from scratch each time.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "rooms.db"
SCHEMA_FILE = HERE / "schema.sql"
SEED_FILE = HERE / "seed_data.sql"

TABLES = ("room_types", "rooms", "beds", "room_arrangements")
MIN_RECORDS = 10  # required by the unit specification


def build(db_path: Path) -> sqlite3.Connection:
    """Apply schema.sql then seed_data.sql to a fresh database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.executescript(SEED_FILE.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def check(conn: sqlite3.Connection) -> list[str]:
    """Return a list of problems found. An empty list means all good."""
    problems: list[str] = []
    q = lambda sql: conn.execute(sql).fetchall()

    for table in TABLES:
        count = q(f"SELECT COUNT(*) FROM {table}")[0][0]
        if count < MIN_RECORDS:
            problems.append(f"{table} has {count} records, the specification requires {MIN_RECORDS}")

    if q("PRAGMA foreign_key_check"):
        problems.append("foreign key violations found")

    orphan_beds = q("""
        SELECT bed_number FROM beds
        WHERE status = 'occupied'
          AND bed_id NOT IN (SELECT bed_id FROM room_arrangements WHERE status = 'In Progress')
    """)
    for (bed,) in orphan_beds:
        problems.append(f"bed {bed} is marked occupied but has no active arrangement")

    stale = q("""
        SELECT a.arrangement_id FROM room_arrangements a
        JOIN beds b ON b.bed_id = a.bed_id
        WHERE a.status = 'In Progress' AND b.status <> 'occupied'
    """)
    for (arr,) in stale:
        problems.append(f"arrangement {arr} is in progress but its bed is not marked occupied")

    overlaps = q("""
        SELECT a.arrangement_id, c.arrangement_id, a.bed_id
        FROM room_arrangements a
        JOIN room_arrangements c
          ON a.bed_id = c.bed_id AND a.arrangement_id < c.arrangement_id
        WHERE a.status IN ('Scheduled', 'In Progress')
          AND c.status IN ('Scheduled', 'In Progress')
          AND a.start_time < COALESCE(c.end_time, '9999')
          AND c.start_time < COALESCE(a.end_time, '9999')
    """)
    for first, second, bed in overlaps:
        problems.append(f"arrangements {first} and {second} overlap on bed {bed}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="path to the SQLite file")
    parser.add_argument("--check", action="store_true", help="run consistency checks after building")
    args = parser.parse_args()

    conn = build(args.db)
    print(f"Created {args.db}")
    for table in TABLES:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<20} {count:>3} records")

    exit_code = 0
    if args.check:
        problems = check(conn)
        print()
        if problems:
            print("Consistency check FAILED:")
            for problem in problems:
                print(f"  - {problem}")
            exit_code = 1
        else:
            print("Consistency check passed.")

    conn.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
