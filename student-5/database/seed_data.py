"""Seed data for the Student 5 database microservice.

Staff & Shift Management. Provides realistic sample records so the backend/API
microservice and its tests have data to work against. Seeding is idempotent at
the script level: ``init_db.py`` seeds only an empty database unless told to
reset.

Record counts exceed the Release 0 minimum of 10 per table.
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Tuple

# --------------------------------------------------------------------------
# STAFF — (name, role, department, specialisation, availability, employment, notes)
# --------------------------------------------------------------------------
STAFF_SEED: List[Tuple] = [
    ("Amara Okafor",      "Registered Nurse", "Emergency",      "Triage",              "Available",   "Full-Time", "Senior triage nurse."),
    ("Daniel Reyes",      "Doctor",           "Emergency",      "Emergency Medicine",  "Available",   "Full-Time", None),
    ("Priya Nandakumar",  "Registered Nurse", "Intensive Care", "Critical Care",       "Available",   "Part-Time", "Works Tuesday to Thursday."),
    ("Liam O'Sullivan",   "Enrolled Nurse",   "General Ward",   None,                  "Available",   "Casual",    None),
    ("Mei Lin Tan",       "Doctor",           "Surgery",        "Anaesthetics",        "On Leave",    "Full-Time", "Annual leave until 2026-09-07."),
    ("Grace Mwangi",      "Midwife",          "Maternity",      "Obstetrics",          "Available",   "Full-Time", None),
    ("Hassan Al-Rashid",  "Registered Nurse", "Surgery",        "Perioperative",       "Available",   "Full-Time", "Theatre-trained."),
    ("Sofia Petrova",     "Physiotherapist",  "Rehabilitation", "Musculoskeletal",     "Available",   "Part-Time", None),
    ("Ethan Brooks",      "Ward Clerk",       "General Ward",   None,                  "Available",   "Casual",    None),
    ("Rina Kobayashi",    "Pharmacist",       "Pharmacy",       "Clinical Pharmacy",   "Available",   "Full-Time", None),
    ("Tomas Novak",       "Radiographer",     "Radiology",      "Diagnostic Imaging",  "Unavailable", "Contract",  "Unavailable pending contract renewal."),
    ("Chloe Bennett",     "Registered Nurse", "Intensive Care", "Critical Care",       "Available",   "Full-Time", None),
]

# --------------------------------------------------------------------------
# SHIFT — (department, date, start, end, required_role, count, status, notes)
# --------------------------------------------------------------------------
SHIFT_SEED: List[Tuple] = [
    ("Emergency",      "2026-08-21", "07:00", "15:00", "Registered Nurse", 2, "Completed", "Day shift."),
    ("Surgery",        "2026-08-22", "08:00", "16:00", "Registered Nurse", 1, "Completed", "Elective theatre list."),
    ("Emergency",      "2026-08-24", "07:00", "15:00", "Registered Nurse", 2, "Filled",    "Day shift."),
    ("Emergency",      "2026-08-24", "15:00", "23:00", "Doctor",           1, "Filled",    "Evening cover."),
    ("Intensive Care", "2026-08-24", "19:00", "07:00", "Registered Nurse", 2, "Filled",    "Overnight shift, crosses midnight."),
    ("Maternity",      "2026-08-25", "07:00", "19:00", "Midwife",          1, "Filled",    "Birthing suite cover."),
    ("General Ward",   "2026-08-25", "14:00", "22:00", "Enrolled Nurse",   1, "Filled",    None),
    ("Rehabilitation", "2026-08-26", "09:00", "17:00", "Physiotherapist",  1, "Filled",    "Outpatient clinic."),
    ("Pharmacy",       "2026-08-26", "08:00", "16:00", "Pharmacist",       1, "Filled",    None),
    ("Radiology",      "2026-08-27", "08:00", "16:00", "Radiographer",     1, "Open",      "Awaiting available radiographer."),
    ("Emergency",      "2026-08-27", "23:00", "07:00", "Registered Nurse", 2, "Planned",   "Night shift, crosses midnight."),
    ("Intensive Care", "2026-08-28", "07:00", "19:00", "Registered Nurse", 2, "Filled",    None),
    ("General Ward",   "2026-08-29", "06:00", "14:00", "Ward Clerk",       1, "Planned",   "Administrative cover."),
]

# --------------------------------------------------------------------------
# SHIFT_ASSIGNMENT — (shift_id, staff_id, status, approved_by, approved_at)
# Staff 5 (on leave) and staff 11 (unavailable) hold no assignments, which
# gives the backend realistic data for availability filtering.
# --------------------------------------------------------------------------
ASSIGNMENT_SEED: List[Tuple] = [
    (1,  1,  "Completed", "Nadia Whitfield", "2026-08-20 09:15"),
    (1,  7,  "Completed", "Nadia Whitfield", "2026-08-20 09:15"),
    (2,  7,  "Completed", "Peter Lang",      "2026-08-21 11:40"),
    (3,  1,  "Confirmed", "Nadia Whitfield", "2026-08-22 08:05"),
    (3,  12, "Assigned",  None,              None),
    (4,  2,  "Confirmed", "Nadia Whitfield", "2026-08-22 08:10"),
    (5,  3,  "Confirmed", "Peter Lang",      "2026-08-22 14:30"),
    (5,  12, "Assigned",  None,              None),
    (6,  6,  "Confirmed", "Helen Barros",    "2026-08-22 16:00"),
    (7,  4,  "Assigned",  None,              None),
    (8,  8,  "Confirmed", "Helen Barros",    "2026-08-23 09:00"),
    (9,  10, "Confirmed", "Peter Lang",      "2026-08-23 09:20"),
    (11, 1,  "Assigned",  None,              None),
    (11, 12, "Declined",  None,              None),
    (12, 3,  "Assigned",  None,              None),
    (12, 12, "Assigned",  None,              None),
    (13, 9,  "Assigned",  None,              None),
]


def seed(connection: sqlite3.Connection) -> Dict[str, int]:
    """Insert all seed records and return the number of rows added per table.

    Inserts run in dependency order (staff and shift before shift_assignment)
    so the foreign keys in ``shift_assignment`` always resolve.
    """
    connection.executemany(
        """
        INSERT INTO staff (
            name, role, department, specialisation,
            availability_status, employment_status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        STAFF_SEED,
    )

    connection.executemany(
        """
        INSERT INTO shift (
            department, shift_date, start_time, end_time,
            required_role, required_staff_count, shift_status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        SHIFT_SEED,
    )

    connection.executemany(
        """
        INSERT INTO shift_assignment (
            shift_id, staff_id, assignment_status, approved_by, approved_at
        ) VALUES (?, ?, ?, ?, ?);
        """,
        ASSIGNMENT_SEED,
    )

    return {
        "staff": len(STAFF_SEED),
        "shift": len(SHIFT_SEED),
        "shift_assignment": len(ASSIGNMENT_SEED),
    }


def is_empty(connection: sqlite3.Connection) -> bool:
    """Return True when no staff records exist yet."""
    return connection.execute("SELECT COUNT(*) FROM staff;").fetchone()[0] == 0
