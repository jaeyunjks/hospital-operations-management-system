"""Data-access layer for the Student 5 database microservice.

Staff & Shift Management. Exposes plain CRUD functions over the three approved
entities so the Flask backend/API microservice can call them later without
touching SQL or the database file directly.

This is the database layer only: no HTTP routes, no authentication, and no
scheduling business rules (for example, overlap detection or AI-assisted
recommendations) live here.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

# Columns a caller is allowed to update, per table. Update statements are built
# from this whitelist so a column name can never be injected by a caller.
UPDATABLE_COLUMNS: Dict[str, tuple] = {
    "staff": (
        "name", "role", "department", "specialisation",
        "availability_status", "employment_status", "notes",
    ),
    "shift": (
        "department", "shift_date", "start_time", "end_time",
        "required_role", "required_staff_count", "shift_status", "notes",
    ),
    "shift_assignment": (
        "shift_id", "staff_id", "assignment_status", "approved_by", "approved_at",
    ),
}

PRIMARY_KEYS = {
    "staff": "staff_id",
    "shift": "shift_id",
    "shift_assignment": "assignment_id",
}


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------
def _rows_to_dicts(rows) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def _update(connection: sqlite3.Connection, table: str, record_id: int,
            **fields: Any) -> bool:
    """Update whitelisted columns of one record. Returns True if a row changed."""
    allowed = UPDATABLE_COLUMNS[table]
    changes = {key: value for key, value in fields.items() if key in allowed}
    if not changes:
        return False

    assignments = ", ".join(f"{column} = ?" for column in changes)
    statement = (
        f"UPDATE {table} SET {assignments} WHERE {PRIMARY_KEYS[table]} = ?;"
    )
    cursor = connection.execute(statement, (*changes.values(), record_id))
    return cursor.rowcount > 0


def _delete(connection: sqlite3.Connection, table: str, record_id: int) -> bool:
    """Delete one record. Returns True if a row was removed."""
    cursor = connection.execute(
        f"DELETE FROM {table} WHERE {PRIMARY_KEYS[table]} = ?;", (record_id,)
    )
    return cursor.rowcount > 0


# --------------------------------------------------------------------------
# STAFF
# --------------------------------------------------------------------------
def create_staff(connection: sqlite3.Connection, name: str, role: str,
                 department: str, specialisation: Optional[str] = None,
                 availability_status: str = "Available",
                 employment_status: str = "Full-Time",
                 notes: Optional[str] = None) -> int:
    cursor = connection.execute(
        """
        INSERT INTO staff (
            name, role, department, specialisation,
            availability_status, employment_status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (name, role, department, specialisation,
         availability_status, employment_status, notes),
    )
    return int(cursor.lastrowid)


def get_staff(connection: sqlite3.Connection, staff_id: int) -> Optional[Dict[str, Any]]:
    row = connection.execute(
        "SELECT * FROM staff WHERE staff_id = ?;", (staff_id,)
    ).fetchone()
    return dict(row) if row else None


def list_staff(connection: sqlite3.Connection, department: Optional[str] = None,
               role: Optional[str] = None,
               availability_status: Optional[str] = None) -> List[Dict[str, Any]]:
    clauses, parameters = [], []
    if department:
        clauses.append("department = ?")
        parameters.append(department)
    if role:
        clauses.append("role = ?")
        parameters.append(role)
    if availability_status:
        clauses.append("availability_status = ?")
        parameters.append(availability_status)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT * FROM staff{where} ORDER BY name;", parameters
    ).fetchall()
    return _rows_to_dicts(rows)


def update_staff(connection: sqlite3.Connection, staff_id: int, **fields: Any) -> bool:
    return _update(connection, "staff", staff_id, **fields)


def delete_staff(connection: sqlite3.Connection, staff_id: int) -> bool:
    return _delete(connection, "staff", staff_id)


# --------------------------------------------------------------------------
# SHIFT
# --------------------------------------------------------------------------
def create_shift(connection: sqlite3.Connection, department: str, shift_date: str,
                 start_time: str, end_time: str, required_role: str,
                 required_staff_count: int = 1, shift_status: str = "Planned",
                 notes: Optional[str] = None) -> int:
    cursor = connection.execute(
        """
        INSERT INTO shift (
            department, shift_date, start_time, end_time,
            required_role, required_staff_count, shift_status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (department, shift_date, start_time, end_time,
         required_role, required_staff_count, shift_status, notes),
    )
    return int(cursor.lastrowid)


def get_shift(connection: sqlite3.Connection, shift_id: int) -> Optional[Dict[str, Any]]:
    row = connection.execute(
        "SELECT * FROM shift WHERE shift_id = ?;", (shift_id,)
    ).fetchone()
    return dict(row) if row else None


def list_shifts(connection: sqlite3.Connection, department: Optional[str] = None,
                shift_date: Optional[str] = None,
                shift_status: Optional[str] = None) -> List[Dict[str, Any]]:
    clauses, parameters = [], []
    if department:
        clauses.append("department = ?")
        parameters.append(department)
    if shift_date:
        clauses.append("shift_date = ?")
        parameters.append(shift_date)
    if shift_status:
        clauses.append("shift_status = ?")
        parameters.append(shift_status)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT * FROM shift{where} ORDER BY shift_date, start_time;", parameters
    ).fetchall()
    return _rows_to_dicts(rows)


def update_shift(connection: sqlite3.Connection, shift_id: int, **fields: Any) -> bool:
    return _update(connection, "shift", shift_id, **fields)


def delete_shift(connection: sqlite3.Connection, shift_id: int) -> bool:
    """Delete a shift. Its assignments are removed by ON DELETE CASCADE."""
    return _delete(connection, "shift", shift_id)


# --------------------------------------------------------------------------
# SHIFT_ASSIGNMENT
# --------------------------------------------------------------------------
def create_assignment(connection: sqlite3.Connection, shift_id: int, staff_id: int,
                      assignment_status: str = "Assigned",
                      approved_by: Optional[str] = None,
                      approved_at: Optional[str] = None) -> int:
    cursor = connection.execute(
        """
        INSERT INTO shift_assignment (
            shift_id, staff_id, assignment_status, approved_by, approved_at
        ) VALUES (?, ?, ?, ?, ?);
        """,
        (shift_id, staff_id, assignment_status, approved_by, approved_at),
    )
    return int(cursor.lastrowid)


def get_assignment(connection: sqlite3.Connection,
                   assignment_id: int) -> Optional[Dict[str, Any]]:
    row = connection.execute(
        "SELECT * FROM shift_assignment WHERE assignment_id = ?;", (assignment_id,)
    ).fetchone()
    return dict(row) if row else None


def list_assignments(connection: sqlite3.Connection, shift_id: Optional[int] = None,
                     staff_id: Optional[int] = None,
                     assignment_status: Optional[str] = None) -> List[Dict[str, Any]]:
    clauses, parameters = [], []
    if shift_id is not None:
        clauses.append("shift_id = ?")
        parameters.append(shift_id)
    if staff_id is not None:
        clauses.append("staff_id = ?")
        parameters.append(staff_id)
    if assignment_status:
        clauses.append("assignment_status = ?")
        parameters.append(assignment_status)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT * FROM shift_assignment{where} ORDER BY assignment_id;", parameters
    ).fetchall()
    return _rows_to_dicts(rows)


def update_assignment(connection: sqlite3.Connection, assignment_id: int,
                      **fields: Any) -> bool:
    return _update(connection, "shift_assignment", assignment_id, **fields)


def delete_assignment(connection: sqlite3.Connection, assignment_id: int) -> bool:
    return _delete(connection, "shift_assignment", assignment_id)


# --------------------------------------------------------------------------
# Relationship queries — join across the M:N relationship
# --------------------------------------------------------------------------
def list_staff_for_shift(connection: sqlite3.Connection,
                         shift_id: int) -> List[Dict[str, Any]]:
    """Return the staff assigned to one shift, with their assignment status."""
    rows = connection.execute(
        """
        SELECT s.staff_id, s.name, s.role, s.department,
               a.assignment_id, a.assignment_status
        FROM shift_assignment AS a
        JOIN staff AS s ON s.staff_id = a.staff_id
        WHERE a.shift_id = ?
        ORDER BY s.name;
        """,
        (shift_id,),
    ).fetchall()
    return _rows_to_dicts(rows)


def list_shifts_for_staff(connection: sqlite3.Connection,
                          staff_id: int) -> List[Dict[str, Any]]:
    """Return the shifts one staff member is assigned to."""
    rows = connection.execute(
        """
        SELECT sh.shift_id, sh.department, sh.shift_date,
               sh.start_time, sh.end_time, sh.shift_status,
               a.assignment_id, a.assignment_status
        FROM shift_assignment AS a
        JOIN shift AS sh ON sh.shift_id = a.shift_id
        WHERE a.staff_id = ?
        ORDER BY sh.shift_date, sh.start_time;
        """,
        (staff_id,),
    ).fetchall()
    return _rows_to_dicts(rows)


def count_rows(connection: sqlite3.Connection, table: str) -> int:
    """Return the row count of one of this service's tables."""
    if table not in PRIMARY_KEYS:
        raise ValueError(f"Unknown table: {table}")
    return int(connection.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0])
