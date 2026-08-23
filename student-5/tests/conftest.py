"""Shared pytest fixtures for the Student 5 test suite.

The backend is tested against a stub database client rather than a live
database service, so the tests exercise routing, validation, error handling,
and application logic without needing a second process or a real SQLite file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from errors import ConflictError, NotFoundError  # noqa: E402


class StubDatabaseClient:
    """In-memory stand-in for the database microservice.

    Mirrors the HTTP client's method surface and the error semantics the real
    service produces (404 -> NotFoundError, 409 -> ConflictError).
    """

    def __init__(self):
        self.staff = {
            1: {"staff_id": 1, "name": "Amara Okafor", "role": "Registered Nurse",
                "department": "Emergency", "specialisation": "Triage",
                "availability_status": "Available", "employment_status": "Full-Time",
                "notes": None},
            2: {"staff_id": 2, "name": "Daniel Reyes", "role": "Doctor",
                "department": "Emergency", "specialisation": "Emergency Medicine",
                "availability_status": "Available", "employment_status": "Full-Time",
                "notes": None},
            3: {"staff_id": 3, "name": "Mei Lin Tan", "role": "Doctor",
                "department": "Surgery", "specialisation": "Anaesthetics",
                "availability_status": "On Leave", "employment_status": "Full-Time",
                "notes": None},
        }
        self.shifts = {
            1: {"shift_id": 1, "department": "Emergency", "shift_date": "2026-08-24",
                "start_time": "07:00", "end_time": "15:00",
                "required_role": "Registered Nurse", "required_staff_count": 2,
                "shift_status": "Planned", "notes": None},
            2: {"shift_id": 2, "department": "Surgery", "shift_date": "2026-08-25",
                "start_time": "08:00", "end_time": "16:00",
                "required_role": "Doctor", "required_staff_count": 1,
                "shift_status": "Planned", "notes": None},
        }
        self.assignments = {
            1: {"assignment_id": 1, "shift_id": 1, "staff_id": 1,
                "assignment_status": "Assigned", "approved_by": None, "approved_at": None},
        }
        self._next_shift_id = 3
        self._next_assignment_id = 2

    # -- health ----------------------------------------------------------
    def health(self):
        return {"status": "ok", "service": "stub"}

    # -- staff -----------------------------------------------------------
    def list_staff(self, department=None, role=None, availability_status=None):
        records = list(self.staff.values())
        if department:
            records = [r for r in records if r["department"] == department]
        if role:
            records = [r for r in records if r["role"] == role]
        if availability_status:
            records = [r for r in records if r["availability_status"] == availability_status]
        return sorted(records, key=lambda r: r["name"])

    def get_staff(self, staff_id):
        if staff_id not in self.staff:
            raise NotFoundError(f"Staff {staff_id} not found.")
        return self.staff[staff_id]

    def update_staff(self, staff_id, **fields):
        record = self.get_staff(staff_id)
        record.update({k: v for k, v in fields.items() if v is not None})
        return record

    def list_shifts_for_staff(self, staff_id):
        return [self.shifts[a["shift_id"]] for a in self.assignments.values()
                if a["staff_id"] == staff_id]

    # -- shifts ----------------------------------------------------------
    def list_shifts(self, department=None, shift_date=None, shift_status=None):
        records = list(self.shifts.values())
        if department:
            records = [r for r in records if r["department"] == department]
        if shift_date:
            records = [r for r in records if r["shift_date"] == shift_date]
        if shift_status:
            records = [r for r in records if r["shift_status"] == shift_status]
        return records

    def get_shift(self, shift_id):
        if shift_id not in self.shifts:
            raise NotFoundError(f"Shift {shift_id} not found.")
        return self.shifts[shift_id]

    def create_shift(self, **fields):
        shift_id = self._next_shift_id
        self._next_shift_id += 1
        record = {"shift_id": shift_id, "required_staff_count": 1,
                  "shift_status": "Planned", "notes": None, **fields}
        self.shifts[shift_id] = record
        return record

    def update_shift(self, shift_id, **fields):
        record = self.get_shift(shift_id)
        record.update(fields)
        return record

    def delete_shift(self, shift_id):
        self.get_shift(shift_id)
        del self.shifts[shift_id]
        for aid in [a["assignment_id"] for a in self.assignments.values()
                    if a["shift_id"] == shift_id]:
            del self.assignments[aid]

    def list_staff_for_shift(self, shift_id):
        return [{**self.staff[a["staff_id"]], "assignment_id": a["assignment_id"],
                 "assignment_status": a["assignment_status"]}
                for a in self.assignments.values() if a["shift_id"] == shift_id]

    # -- assignments -----------------------------------------------------
    def list_assignments(self, shift_id=None, staff_id=None, assignment_status=None):
        records = list(self.assignments.values())
        if shift_id is not None:
            records = [r for r in records if r["shift_id"] == shift_id]
        if staff_id is not None:
            records = [r for r in records if r["staff_id"] == staff_id]
        if assignment_status:
            records = [r for r in records if r["assignment_status"] == assignment_status]
        return records

    def create_assignment(self, **fields):
        duplicate = [a for a in self.assignments.values()
                     if a["shift_id"] == fields["shift_id"]
                     and a["staff_id"] == fields["staff_id"]]
        if duplicate:
            raise ConflictError("Assignment already exists.")
        assignment_id = self._next_assignment_id
        self._next_assignment_id += 1
        record = {"assignment_id": assignment_id, "assignment_status": "Assigned",
                  "approved_by": None, "approved_at": None, **fields}
        self.assignments[assignment_id] = record
        return record

    def update_assignment(self, assignment_id, **fields):
        if assignment_id not in self.assignments:
            raise NotFoundError(f"Assignment {assignment_id} not found.")
        self.assignments[assignment_id].update(fields)
        return self.assignments[assignment_id]

    def delete_assignment(self, assignment_id):
        self.assignments.pop(assignment_id, None)


@pytest.fixture
def stub_database(monkeypatch):
    """Replace the HTTP database client with the in-memory stub."""
    stub = StubDatabaseClient()
    import database_client
    import services.ai_service as ai_service
    import services.assignment_service as assignment_service
    import services.coverage_service as coverage_service
    import services.shift_service as shift_service
    import services.staff_service as staff_service

    for module in (database_client, ai_service, assignment_service,
                   coverage_service, shift_service, staff_service):
        monkeypatch.setattr(module, "database_client", stub, raising=False)
    return stub


@pytest.fixture
def client(stub_database):
    """Flask test client wired to the stub database."""
    from app import create_app
    application = create_app()
    application.config.update(TESTING=True)
    with application.test_client() as test_client:
        yield test_client
