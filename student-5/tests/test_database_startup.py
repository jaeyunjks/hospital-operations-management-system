"""Regression coverage for persistent Student 5 database startup."""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

DATABASE_DIR = Path(__file__).resolve().parents[1] / "database"
sys.path.insert(0, str(DATABASE_DIR))

import db as database_db  # noqa: E402
import init_db as database_init  # noqa: E402
import seed_data as database_seed  # noqa: E402
import service as database_service  # noqa: E402


def test_connection_uses_bounded_lock_wait_and_rollback_journal(tmp_path):
    database_path = tmp_path / "staff_shift.db"

    with database_db.get_connection(database_path) as connection:
        assert connection.execute("PRAGMA busy_timeout;").fetchone()[0] == 30_000
        assert connection.execute("PRAGMA foreign_keys;").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode;").fetchone()[0] == "delete"

    assert not database_path.with_name(f"{database_path.name}-wal").exists()


def test_azure_files_mode_uses_nolock_uri(tmp_path, monkeypatch):
    database_path = (tmp_path / "staff_shift.db").resolve()
    calls = []
    original_connect = sqlite3.connect

    def record_connect(target, *args, **kwargs):
        calls.append((target, kwargs.copy()))
        return original_connect(target, *args, **kwargs)

    monkeypatch.setenv(database_db.SQLITE_NOLOCK_ENV_VAR, "true")
    monkeypatch.setattr(database_db.sqlite3, "connect", record_connect)

    with database_db.get_connection(database_path) as connection:
        connection.execute("CREATE TABLE probe (value INTEGER);")

    assert calls[0][0] == f"{database_path.as_uri()}?nolock=1"
    assert calls[0][1]["uri"] is True


def test_azure_files_mode_serialises_connections(tmp_path, monkeypatch):
    database_path = (tmp_path / "staff_shift.db").resolve()
    monkeypatch.setenv(database_db.SQLITE_NOLOCK_ENV_VAR, "true")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def hold_first_connection():
        with database_db.get_connection(database_path) as connection:
            connection.execute("CREATE TABLE probe (value INTEGER);")
            first_entered.set()
            release_first.wait(timeout=2)

    def open_second_connection():
        with database_db.get_connection(database_path):
            second_entered.set()

    first = threading.Thread(target=hold_first_connection)
    second = threading.Thread(target=open_second_connection)
    first.start()
    assert first_entered.wait(timeout=1)
    second.start()
    assert not second_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert second_entered.is_set()


def test_azure_files_mode_requires_absolute_database_path(monkeypatch):
    monkeypatch.setenv(database_db.SQLITE_NOLOCK_ENV_VAR, "true")

    with pytest.raises(RuntimeError, match="requires an absolute database path"):
        database_db.connect("relative.db")


def test_interrupted_first_startup_journal_is_preserved_before_initialisation(
    tmp_path,
):
    database_path = tmp_path / "staff_shift.db"
    database_path.touch()
    journal = tmp_path / "staff_shift.db-journal"
    journal.write_bytes(bytes(512))

    database_init.initialise(database_path)

    backup = tmp_path / "staff_shift.db-journal.interrupted"
    assert backup.read_bytes() == bytes(512)
    assert (tmp_path / "staff_shift.db.interrupted-empty").read_bytes() == b""
    assert not journal.exists()
    with database_db.get_connection(database_path) as connection:
        assert database_db.schema_is_current(connection)
        assert connection.execute("SELECT COUNT(*) FROM staff;").fetchone()[0] == 12


def test_empty_database_is_built_off_mount_before_safe_publication(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "mounted" / "staff_shift.db"
    database_path.parent.mkdir()
    database_path.touch()
    connected_paths = []
    original_connect = database_db.connect

    def record_connection(path=None):
        connected_paths.append(Path(path) if path is not None else None)
        assert Path(path) != database_path
        return original_connect(path)

    monkeypatch.setattr(database_db, "connect", record_connection)

    database_init.initialise(database_path)

    assert connected_paths
    assert database_path.stat().st_size > 0
    assert (database_path.parent / "staff_shift.db.interrupted-empty").exists()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM staff;").fetchone()[0] == 12


def test_nonempty_or_page_bearing_database_state_is_never_auto_recovered(tmp_path):
    populated = tmp_path / "populated.db"
    populated.write_bytes(b"existing-data")
    populated_journal = tmp_path / "populated.db-journal"
    populated_journal.write_bytes(bytes(512))

    assert database_init.preserve_interrupted_empty_journal(populated) is None
    assert populated.read_bytes() == b"existing-data"
    assert populated_journal.read_bytes() == bytes(512)

    empty = tmp_path / "empty.db"
    empty.touch()
    page_bearing_journal = tmp_path / "empty.db-journal"
    page_bearing_journal.write_bytes(bytes(513))

    with pytest.raises(RuntimeError, match="Refusing automatic recovery"):
        database_init.preserve_interrupted_empty_journal(empty)
    assert page_bearing_journal.read_bytes() == bytes(513)


def test_existing_persistent_database_is_not_reseeded_or_rewritten(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "staff_shift.db"
    database_init.initialise(database_path)

    with database_db.get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO staff (
                name, role, department, availability_status, employment_status
            ) VALUES (?, ?, ?, ?, ?);
            """,
            ("Persistent Manager", "Staff Manager", "Emergency",
             "Available", "Full-Time"),
        )
        count_before = connection.execute(
            "SELECT COUNT(*) FROM staff;"
        ).fetchone()[0]

    def unexpected_schema_write(_connection):
        pytest.fail("current persistent schema must not be rewritten")

    def unexpected_seed(_connection):
        pytest.fail("non-empty persistent data must not be reseeded")

    monkeypatch.setattr(database_db, "apply_schema", unexpected_schema_write)
    monkeypatch.setattr(database_seed, "seed", unexpected_seed)

    database_init.initialise(database_path)

    with database_db.get_connection(database_path) as connection:
        count_after = connection.execute(
            "SELECT COUNT(*) FROM staff;"
        ).fetchone()[0]
        preserved = connection.execute(
            "SELECT name FROM staff WHERE name = ?;", ("Persistent Manager",)
        ).fetchone()

    assert count_after == count_before
    assert preserved["name"] == "Persistent Manager"


def test_startup_waits_for_transient_lock_then_health_reads_persistent_data(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "staff_shift.db"
    database_init.initialise(database_path)

    blocker = sqlite3.connect(database_path)
    blocker.execute("BEGIN EXCLUSIVE;")
    errors = []

    def initialise_while_locked():
        try:
            database_init.initialise(database_path)
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    startup = threading.Thread(target=initialise_while_locked)
    startup.start()
    time.sleep(0.1)
    assert startup.is_alive(), "startup should wait rather than fail immediately"

    blocker.rollback()
    blocker.close()
    startup.join(timeout=5)

    assert not startup.is_alive()
    assert errors == []

    monkeypatch.setenv(database_db.DATABASE_PATH_ENV_VAR, str(database_path))
    response = database_service.create_app().test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    assert response.get_json()["counts"] == {
        "staff": 12,
        "shift": 13,
        "shift_assignment": 17,
    }


def test_liveness_probe_does_not_open_sqlite(monkeypatch):
    def unexpected_connection(*_args, **_kwargs):
        pytest.fail("process liveness must not open the persistent database")

    monkeypatch.setattr(database_db, "get_connection", unexpected_connection)

    response = database_service.create_app().test_client().get("/live")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "service": "student-5-database",
    }
