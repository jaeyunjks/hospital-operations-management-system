# Creates and populates the Patient & Admissions Management Database
# Creation date: 29/08/2026

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = Path(__file__).resolve().parent / "patients.db"
SCHEMA_FILE = HERE / "schema.sql"
SEED_FILE = HERE / "seed_data.sql"

TABLES = ("patients", "patient_addresses", "patient_medical_information", "patient_contacts", "patient_admin_notes", "admissions")
MIN_RECORDS = 10

def build(db_path: Path) -> sqlite3.Connection:
    # Apply schema.sql then seed_data.sql to a fresh database
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
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