"""Database start-up on a persistent volume (Azure Files in the cloud).

SQLite cannot lock files on Azure Files, so the container seeds the database
on local disk and copies it into place, and the service opens it without
locks when STUDENT4_SQLITE_NOLOCK is set.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

DATABASE_DIR = Path(__file__).resolve().parent.parent / "database"
INIT_DB = DATABASE_DIR / "init_db.py"


def run_init(db_path):
    return subprocess.run(  # noqa: S603 - fixed interpreter and repository script
        [sys.executable, str(INIT_DB), "--db", str(db_path), "--if-needed"],
        capture_output=True, text=True, check=True,
    ).stdout


def tables(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_if_needed_seeds_a_missing_database(tmp_path):
    db_path = tmp_path / "data" / "rooms.db"
    assert "built locally" in run_init(db_path)
    assert {"room_types", "rooms", "beds", "room_arrangements", "shortage_cases"} <= tables(db_path)


def test_if_needed_repairs_a_file_without_tables(tmp_path):
    # What a failed first start on Azure Files left behind: a file with no schema.
    db_path = tmp_path / "rooms.db"
    sqlite3.connect(db_path).execute("CREATE TABLE placeholder (x)").connection.close()
    assert "built locally" in run_init(db_path)
    assert "room_types" in tables(db_path)


def test_if_needed_keeps_existing_data(tmp_path):
    db_path = tmp_path / "rooms.db"
    run_init(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE rooms SET notes = 'kept across restart' WHERE room_id = 1")
    conn.commit()
    conn.close()

    assert "Using existing database" in run_init(db_path)
    conn = sqlite3.connect(db_path)
    note = conn.execute("SELECT notes FROM rooms WHERE room_id = 1").fetchone()[0]
    conn.close()
    assert note == "kept across restart"


def test_nolock_mode_serves_requests(database_app, monkeypatch):
    import db
    monkeypatch.setattr(db, "NOLOCK", True)
    client = database_app.test_client()
    first = client.get("/health")
    second = client.get("/health")
    assert first.status_code == second.status_code == 200
    # The per-process lock is released after every request.
    assert db._nolock_lock.acquire(blocking=False)
    db._nolock_lock.release()
