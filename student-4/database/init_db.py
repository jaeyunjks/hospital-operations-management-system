"""
Create and populate the Room & Bed Management database.

Usage
-----
    python init_db.py              # create rooms.db in this directory
    python init_db.py --check      # create, then run consistency checks
    python init_db.py --db /path/to/rooms.db
    python init_db.py --db /data/rooms.db --if-needed   # container start

Safe to re-run: schema.sql drops every table before recreating it.
--if-needed leaves a database that already has every table untouched, so a
persistent volume keeps its data across restarts.
"""

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = Path(__file__).resolve().parent / "rooms.db"
SCHEMA_FILE = HERE / "schema.sql"
SEED_FILE = HERE / "seed_data.sql"

TABLES = ("room_types", "rooms", "beds", "room_arrangements", "shortage_cases")
MIN_RECORDS = 10  # required by the unit specification

ACTIVE = ("Scheduled", "In Progress")


def build(db_path: Path) -> sqlite3.Connection:
    """Apply schema.sql then seed_data.sql to a fresh database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.executescript(SEED_FILE.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def has_schema(db_path: Path) -> bool:
    """True when the file exists and already holds every table."""
    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro&nolock=1", uri=True)
        try:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False
    return set(TABLES) <= names


def build_and_copy(db_path: Path) -> None:
    """Build in a local temporary file, then copy it into place.

    SQLite cannot lock files on Azure Files (SMB), so building directly on the
    mounted share can leave an empty or half-built file. Building on local
    disk and copying one finished file avoids that.
    """
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / db_path.name
        conn = build(local)
        problems = check(conn)
        conn.close()
        if problems:
            raise SystemExit("Seed data failed its consistency check: " + "; ".join(problems))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, db_path)


def check(conn: sqlite3.Connection) -> list:
    """Return a list of problems. An empty list means the data is sound."""
    problems = []

    def q(sql, params=()):
        return conn.execute(sql, params).fetchall()

    for table in TABLES:
        count = q("SELECT COUNT(*) FROM " + table)[0][0]
        if count < MIN_RECORDS:
            problems.append(
                f"{table} has {count} records; the specification requires {MIN_RECORDS}"
            )

    if q("PRAGMA foreign_key_check"):
        problems.append("foreign key violations found")

    for (bed,) in q("""
        SELECT bed_number FROM beds
        WHERE status = 'occupied'
          AND bed_id NOT IN (SELECT bed_id FROM room_arrangements WHERE status = 'In Progress')
    """):
        problems.append(f"bed {bed} is marked occupied but has no active arrangement")

    for (arr,) in q("""
        SELECT a.arrangement_id FROM room_arrangements a
        JOIN beds b ON b.bed_id = a.bed_id
        WHERE a.status = 'In Progress' AND b.status <> 'occupied'
    """):
        problems.append(f"arrangement {arr} is in progress but its bed is not occupied")

    for first, second, bed in q("""
        SELECT a.arrangement_id, c.arrangement_id, a.bed_id
        FROM room_arrangements a
        JOIN room_arrangements c
          ON a.bed_id = c.bed_id AND a.arrangement_id < c.arrangement_id
        WHERE a.status IN ('Scheduled', 'In Progress')
          AND c.status IN ('Scheduled', 'In Progress')
          AND a.start_time < COALESCE(c.end_time, '9999')
          AND c.start_time < COALESCE(a.end_time, '9999')
    """):
        problems.append(f"arrangements {first} and {second} overlap on bed {bed}")

    for room, status, occupied in q("""
        SELECT r.room_number, r.status, SUM(b.status = 'occupied')
        FROM rooms r JOIN beds b ON b.room_id = r.room_id
        GROUP BY r.room_id
        HAVING (r.status = 'In Use'    AND SUM(b.status = 'occupied') = 0)
            OR (r.status = 'Available' AND SUM(b.status = 'occupied') > 0)
    """):
        problems.append(f"room {room} is '{status}' but has {occupied} occupied bed(s)")

    for (case_id,) in q("""
        SELECT case_id FROM shortage_cases
        WHERE status = 'Resolved' AND (resolved_at IS NULL OR decided_by IS NULL)
    """):
        problems.append(f"shortage case {case_id} is resolved without a decision record")

    for (arr,) in q("""
        SELECT a.arrangement_id FROM room_arrangements a
        JOIN room_arrangements p ON p.arrangement_id = a.transferred_from_id
        WHERE a.patient_id <> p.patient_id
    """):
        problems.append(f"transfer {arr} points at an arrangement for a different patient")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="path to the SQLite file")
    parser.add_argument("--check", action="store_true", help="run consistency checks")
    parser.add_argument(
        "--if-needed",
        action="store_true",
        help="only build when the file is missing or has no schema; build locally then copy",
    )
    args = parser.parse_args()

    if args.if_needed:
        if has_schema(args.db):
            print("Using existing database " + str(args.db))
            return 0
        build_and_copy(args.db)
        print("Created " + str(args.db) + " (built locally, then copied)")
        return 0

    conn = build(args.db)
    print("Created " + str(args.db))
    for table in TABLES:
        count = conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        print("  {:<20} {:>3} records".format(table, count))

    exit_code = 0
    if args.check:
        problems = check(conn)
        print()
        if problems:
            print("Consistency check FAILED:")
            for problem in problems:
                print("  - " + problem)
            exit_code = 1
        else:
            print("Consistency check passed.")

    conn.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
