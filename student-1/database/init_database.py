# Creates and populates the Patient & Admissions Management Database
# Creation date: 29/08/2026

import argparse
import sqlite3
import sys
from pathlib import Path

try:
    from database.database import drop_all_tables
except ModuleNotFoundError:
    from database import drop_all_tables

HERE = Path(__file__).resolve().parent
DEFAULT_DB = Path(__file__).resolve().parent / "patients.db"
DB_HELPER = HERE / "database.py"
SCHEMA_FILE = HERE / "schema.sql"
SEED_FILE = HERE / "seed_data.sql"

TABLES = ("patients", "patient_addresses", "patient_medical_information", "patient_contacts", "patient_admin_notes", "admissions")
MIN_RECORDS = 10

def build(db_path: Path) -> sqlite3.Connection:
    # Apply schema.sql then seed_data.sql to a fresh database
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # Clear all existing tables before re-creation
    drop_all_tables(conn)

    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.executescript(SEED_FILE.read_text(encoding="utf-8"))
    conn.commit()
    
    return conn

def healthCheck(conn: sqlite3.Connection) -> bool:
    # Check that each table has at least MIN_RECORDS records
    for table in TABLES:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count < MIN_RECORDS:
            print(f"Health check failed: {table} has only {count} records (minimum required is {MIN_RECORDS})")
            return False
    print("Health check passed: All tables have sufficient records.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and seed the patient database")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    parser.add_argument("--check", action="store_true", help="Run the seed health check after building")
    args = parser.parse_args()

    conn = build(args.db)
    try:
        if args.check and not healthCheck(conn):
            return 1
    finally:
        conn.close()
    print(f"Database built and seeded at {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())