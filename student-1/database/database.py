# SQLite access helper for the Patient & Admissions Management System
# Creation date: 30/08/2026

import os
import sqlite3
from pathlib import Path

from flask import g

DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).resolve().parent / "patients.db"))

# Data Error
class DataError(Exception):

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status

# Return the requested database connection, creating it if necessary
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

# Close the database connection if it exists
def close_db(e=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()

# Run a SELECT and return the results as a list of dictionaries
def query_db(query: str, arguments=(), one=False):
    cursor = get_db().execute(query, arguments)
    returnValue = cursor.fetchall()
    cursor.close()

    # If 'one' is True, return a single record or None if no records found. Otherwise, return all records.
    if one:
        if returnValue:
            return returnValue[0]
        else:
            return None
    return returnValue

# Run a write, commit, and return the last inserted row ID and the number of affected rows
def write_db(query: str, arguments=()):
    conn = get_db()
    try:
        cursor = conn.execute(query, arguments)
        conn.commit()
        return cursor.lastrowid, cursor.rowcount
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise DataError("Database constraint error: {}".format(e)) from e

# Drop all tables in the database if they exist
def drop_all_tables(conn: sqlite3.Connection) -> None:
    for table in ("admissions", 
                  "patient_admin_notes", 
                  "patient_contacts", 
                  "patient_medical_information", 
                  "patient_addresses", 
                  "patients"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")