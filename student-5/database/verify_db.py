#!/usr/bin/env python3
"""Verify the Student 5 database against the approved validation criteria.

Staff & Shift Management. Checks the criteria recorded in
``docs/prompts/student-5/database-development.md`` (S5-DB-001) and prints
evidence suitable for the technical report.

Usage:
    python3 verify_db.py
    python3 verify_db.py --path /tmp/x.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db           # noqa: E402
import repository   # noqa: E402

MINIMUM_RECORDS = 10
REQUIRED_TABLES = ("staff", "shift", "shift_assignment")

results: list[tuple[bool, str]] = []


def check(passed: bool, description: str) -> None:
    results.append((passed, description))
    print(f"  [{'PASS' if passed else 'FAIL'}] {description}")


def verify(database_path=None) -> bool:
    target = Path(database_path) if database_path else db.get_database_path()
    if not target.exists():
        print(f"Database not found at {target}. Run init_db.py first.", file=sys.stderr)
        return False

    with db.get_connection(target) as connection:
        print(f"Verifying {target}\n")

        print("Database structure")
        existing = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table';"
            ).fetchall()
        }
        for table in REQUIRED_TABLES:
            check(table in existing, f"table '{table}' exists")

        for table in REQUIRED_TABLES:
            primary = [
                column["name"]
                for column in connection.execute(f"PRAGMA table_info({table});").fetchall()
                if column["pk"]
            ]
            check(primary == [repository.PRIMARY_KEYS[table]],
                  f"'{table}' primary key is {repository.PRIMARY_KEYS[table]}")

        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(shift_assignment);"
        ).fetchall()
        referenced = {row["table"]: row["to"] for row in foreign_keys}
        check(referenced.get("shift") == "shift_id",
              "shift_assignment.shift_id references shift(shift_id)")
        check(referenced.get("staff") == "staff_id",
              "shift_assignment.staff_id references staff(staff_id)")

        print("\nData population")
        for table in REQUIRED_TABLES:
            count = repository.count_rows(connection, table)
            check(count >= MINIMUM_RECORDS,
                  f"'{table}' holds {count} records (minimum {MINIMUM_RECORDS})")

        print("\nRelationship integrity")
        orphan_shifts = connection.execute(
            """
            SELECT COUNT(*) FROM shift_assignment AS a
            LEFT JOIN shift AS s ON s.shift_id = a.shift_id
            WHERE s.shift_id IS NULL;
            """
        ).fetchone()[0]
        check(orphan_shifts == 0, "no assignment references a missing shift")

        orphan_staff = connection.execute(
            """
            SELECT COUNT(*) FROM shift_assignment AS a
            LEFT JOIN staff AS s ON s.staff_id = a.staff_id
            WHERE s.staff_id IS NULL;
            """
        ).fetchone()[0]
        check(orphan_staff == 0, "no assignment references a missing staff member")

        duplicates = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT shift_id, staff_id FROM shift_assignment
                GROUP BY shift_id, staff_id HAVING COUNT(*) > 1
            );
            """
        ).fetchone()[0]
        check(duplicates == 0, "no staff member is assigned to the same shift twice")

        print("\nQueryability (Staff 1:M ShiftAssignment M:1 Shift)")
        for_shift = repository.list_staff_for_shift(connection, 3)
        check(len(for_shift) > 0,
              f"shift 3 resolves to {len(for_shift)} assigned staff member(s)")
        for_staff = repository.list_shifts_for_staff(connection, 1)
        check(len(for_staff) > 0,
              f"staff 1 resolves to {len(for_staff)} assigned shift(s)")

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed.")
    return passed == total


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Student 5 Staff & Shift Management database."
    )
    parser.add_argument("--path", default=None, help="database file path")
    arguments = parser.parse_args()
    return 0 if verify(arguments.path) else 1


if __name__ == "__main__":
    raise SystemExit(main())
