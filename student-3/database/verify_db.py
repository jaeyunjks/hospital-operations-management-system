#!/usr/bin/env python3
"""Verify Student 3's Pharmacy & Medication Inventory database without changes.

Usage:
    python3 verify_db.py
    python3 verify_db.py --path /tmp/pharmacy.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import db

MINIMUM_RECORDS = 10
PRIMARY_KEYS = {
    "staff": "staff_id",
    "suppliers": "supplier_id",
    "medicines": "medicine_id",
    "batches": "batch_id",
    "purchase_orders": "po_id",
    "stock_movements": "movement_id",
}
REQUIRED_TABLES = tuple(PRIMARY_KEYS)
RELATIONSHIPS = (
    ("medicines", "supplier_id", "suppliers", "supplier_id"),
    ("batches", "medicine_id", "medicines", "medicine_id"),
    ("purchase_orders", "medicine_id", "medicines", "medicine_id"),
    ("purchase_orders", "supplier_id", "suppliers", "supplier_id"),
    ("stock_movements", "medicine_id", "medicines", "medicine_id"),
    ("stock_movements", "batch_id", "batches", "batch_id"),
)


def verify(database_path: str | Path | None = None) -> bool:
    target = Path(database_path) if database_path is not None else db.get_database_path()
    if not target.is_file():
        print(f"Database not found at {target}. Run init_db.py first.", file=sys.stderr)
        return False

    results: list[bool] = []

    def check(passed: bool, description: str) -> None:
        results.append(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {description}")

    def check_count(connection, query: str, description: str, minimum: int = 0) -> None:
        try:
            count = connection.execute(query).fetchone()[0]
            passed = count >= minimum if minimum else count == 0
            check(passed, f"{description} (found {count})")
        except sqlite3.Error as error:
            check(False, f"{description}: {error}")

    connection = None
    try:
        connection = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        print(f"Verifying {target}\n")
        print("Database structure")
        existing = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table';"
            )
        }
        for table in REQUIRED_TABLES:
            check(table in existing, f"table '{table}' exists")
            primary = [
                row["name"] for row in connection.execute(f"PRAGMA table_info({table});")
                if row["pk"]
            ]
            check(primary == [PRIMARY_KEYS[table]],
                  f"'{table}' primary key is {PRIMARY_KEYS[table]}")

        for child, column, parent, key in RELATIONSHIPS:
            foreign_keys = {
                (row["from"], row["table"], row["to"])
                for row in connection.execute(f"PRAGMA foreign_key_list({child});")
            }
            check((column, parent, key) in foreign_keys,
                  f"{child}.{column} references {parent}({key})")

        print("\nData population")
        for table in REQUIRED_TABLES:
            check_count(connection, f"SELECT COUNT(*) FROM {table};",
                        f"'{table}' holds at least {MINIMUM_RECORDS} records",
                        MINIMUM_RECORDS)
        for role, minimum in (("manager", 2), ("staff", 10)):
            check_count(connection,
                        f"SELECT COUNT(*) FROM staff WHERE role = '{role}';",
                        f"at least {minimum} staff records have role '{role}'", minimum)

        print("\nRelationship integrity")
        violations = connection.execute("PRAGMA foreign_key_check;").fetchall()
        check(not violations, f"no foreign-key violations (found {len(violations)})")
        for child, column, parent, key in RELATIONSHIPS:
            check_count(connection, f"""
                SELECT COUNT(*) FROM {child} AS c
                LEFT JOIN {parent} AS p ON c.{column} = p.{key}
                WHERE c.{column} IS NOT NULL AND p.{key} IS NULL;
            """, f"no {child}.{column} references a missing {parent} record")
        check_count(connection, """
            SELECT COUNT(*) FROM (
                SELECT medicine_id, batch_number FROM batches
                GROUP BY medicine_id, batch_number HAVING COUNT(*) > 1
            );
        """, "no duplicate batch numbers for the same medicine")
        check_count(connection, """
            SELECT COUNT(*) FROM stock_movements AS s
            JOIN batches AS b ON s.batch_id = b.batch_id
            WHERE s.medicine_id != b.medicine_id;
        """, "stock movements reference batches for the same medicine")

        print("\nQueryability")
        for child, column, parent, key in RELATIONSHIPS:
            check_count(connection, f"""
                SELECT COUNT(*) FROM {child} AS c
                JOIN {parent} AS p ON c.{column} = p.{key};
            """, f"{child}.{column} resolves to {parent} records", minimum=1)
    except (sqlite3.Error, OSError) as error:
        check(False, f"database verification failed: {error}")
    finally:
        if connection is not None:
            connection.close()

    print(f"\n{sum(results)}/{len(results)} checks passed.")
    return all(results)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Student 3 Pharmacy & Medication Inventory database."
    )
    parser.add_argument("--path", default=None, help="database file path")
    arguments = parser.parse_args()
    return 0 if verify(arguments.path) else 1


if __name__ == "__main__":
    raise SystemExit(main())
