"""The seed data must satisfy the specification and stay self-consistent."""

import sqlite3

import pytest

MIN_RECORDS = 10
TABLES = ("room_types", "rooms", "beds", "room_arrangements", "shortage_cases")


@pytest.fixture()
def connection(database_app):
    import db
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.mark.parametrize("table", TABLES)
def test_minimum_ten_records(connection, table):
    count = connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
    assert count >= MIN_RECORDS, "{} has only {} records".format(table, count)


def test_no_foreign_key_violations(connection):
    connection.execute("PRAGMA foreign_keys = ON")
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_occupied_beds_have_an_active_arrangement(connection):
    orphans = connection.execute("""
        SELECT bed_number FROM beds
        WHERE status = 'occupied'
          AND bed_id NOT IN (SELECT bed_id FROM room_arrangements WHERE status = 'In Progress')
    """).fetchall()
    assert orphans == []


def test_no_overlapping_active_arrangements(connection):
    clashes = connection.execute("""
        SELECT a.arrangement_id, c.arrangement_id FROM room_arrangements a
        JOIN room_arrangements c
          ON a.bed_id = c.bed_id AND a.arrangement_id < c.arrangement_id
        WHERE a.status IN ('Scheduled', 'In Progress')
          AND c.status IN ('Scheduled', 'In Progress')
          AND a.start_time < COALESCE(c.end_time, '9999')
          AND c.start_time < COALESCE(a.end_time, '9999')
    """).fetchall()
    assert clashes == []


def test_three_theatre_states_are_demonstrable(connection):
    """The board must be able to show in use, free and unusable."""
    statuses = {row["status"] for row in connection.execute("""
        SELECT r.status FROM rooms r JOIN room_types rt ON rt.type_id = r.type_id
        WHERE rt.type_name = 'Operating Theatre'
    """)}
    assert {"In Use", "Available", "Out of Service"} <= statuses


def test_check_constraints_reject_invalid_enums(connection):
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO beds (room_id, bed_number, status) VALUES (1, 'X-1', 'sleeping')"
        )
