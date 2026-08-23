"""Model definitions for the Student 5 database microservice.

Staff & Shift Management. Each dataclass mirrors one table in ``schema.sql``
and provides conversion helpers between database rows and plain Python
objects. These are data definitions only — no persistence logic lives here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

# --------------------------------------------------------------------------
# Permitted values — mirror the CHECK constraints declared in schema.sql
# --------------------------------------------------------------------------
AVAILABILITY_STATUSES = ("Available", "Unavailable", "On Leave")
EMPLOYMENT_STATUSES = ("Full-Time", "Part-Time", "Casual", "Contract")
SHIFT_STATUSES = ("Planned", "Open", "Filled", "Completed", "Cancelled")
ASSIGNMENT_STATUSES = ("Assigned", "Confirmed", "Declined", "Cancelled", "Completed")

TABLES = ("staff", "shift", "shift_assignment")


@dataclass
class Staff:
    """A member of the hospital workforce (table: ``staff``)."""

    name: str
    role: str
    department: str
    specialisation: Optional[str] = None
    availability_status: str = "Available"
    employment_status: str = "Full-Time"
    notes: Optional[str] = None
    staff_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Staff":
        return cls(**{key: row[key] for key in row.keys()})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Shift:
    """A planned hospital shift (table: ``shift``)."""

    department: str
    shift_date: str      # ISO 8601 date: YYYY-MM-DD
    start_time: str      # 24-hour time: HH:MM
    end_time: str        # 24-hour time: HH:MM (may cross midnight)
    required_role: str
    required_staff_count: int = 1
    shift_status: str = "Planned"
    notes: Optional[str] = None
    shift_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Shift":
        return cls(**{key: row[key] for key in row.keys()})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ShiftAssignment:
    """Allocation of one staff member to one shift (table: ``shift_assignment``).

    Resolves the many-to-many relationship:
    ``Staff 1:M ShiftAssignment M:1 Shift``.
    """

    shift_id: int
    staff_id: int
    assignment_status: str = "Assigned"
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    assignment_id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ShiftAssignment":
        return cls(**{key: row[key] for key in row.keys()})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
