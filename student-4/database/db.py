"""SQLite access helpers for the database microservice.

Only this service opens the SQLite file. Every other service — including
this feature's own backend — reaches the data through the HTTP API in
app.py, as required by the HOMS collaboration rules.
"""

import os
import sqlite3
import threading
from pathlib import Path

from flask import g

DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).resolve().parent / "rooms.db"))

# Azure Files (SMB) cannot complete SQLite's byte-range locking. The cloud
# deployment sets STUDENT4_SQLITE_NOLOCK, so SQLite opens the file without
# locks and this process serialises every request's connection instead.
# Local and Docker runs keep SQLite's normal locking.
NOLOCK = os.getenv("STUDENT4_SQLITE_NOLOCK", "").strip().lower() in {"1", "true", "yes", "on"}
BUSY_TIMEOUT_SECONDS = 30
_nolock_lock = threading.RLock()


def connect(path):
    """Open the SQLite file, without file locks when NOLOCK is set."""
    if NOLOCK:
        uri = Path(path).resolve().as_uri() + "?nolock=1"
        return sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    return sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)


class DataError(Exception):
    """A request that the data layer can reject cleanly (400/404)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def get_connection():
    """Return this request's connection, opening it on first use."""
    if "db" not in g:
        if not DB_PATH.exists():
            raise DataError(
                "Database not found at {}. Run: python init_db.py --check".format(DB_PATH),
                status=500,
            )
        if NOLOCK:
            _nolock_lock.acquire()
        try:
            g.db = connect(DB_PATH)
        except Exception:
            if NOLOCK:
                _nolock_lock.release()
            raise
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_connection(_exception=None):
    """Close the request's connection. Registered as a teardown handler."""
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()
        if NOLOCK:
            _nolock_lock.release()


def query(sql, params=()):
    """Run a SELECT and return every row as a dictionary."""
    return [dict(row) for row in get_connection().execute(sql, params).fetchall()]


def query_one(sql, params=()):
    """Run a SELECT and return the first row, or None."""
    row = get_connection().execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql, params=()):
    """Run a write, commit, and return (lastrowid, rowcount).

    sqlite3.IntegrityError becomes a 400 so a bad request — a duplicate
    room number, an unknown foreign key, a value outside a CHECK
    constraint — is reported as a client error rather than a crash.
    """
    connection = get_connection()
    try:
        cursor = connection.execute(sql, params)
        connection.commit()
        return cursor.lastrowid, cursor.rowcount
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise DataError("Database constraint failed: {}".format(error)) from error
