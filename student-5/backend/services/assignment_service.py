"""Assignment application logic for the Student 5 backend/API microservice.

Covers allocating staff to shifts and withdrawing those allocations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from database_client import database_client
from errors import ConflictError, NotFoundError, ValidationError

#: Assignments in these states no longer occupy a place on the shift.
INACTIVE_STATUSES = ("Cancelled", "Declined")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def active_assignments(shift_id: int) -> List[Dict[str, Any]]:
    """Return the assignments that currently count towards a shift's coverage."""
    return [
        assignment for assignment in database_client.list_assignments(shift_id=shift_id)
        if assignment["assignment_status"] not in INACTIVE_STATUSES
    ]


def assign_staff(shift_id: int, staff_id: Any,
                 approved_by: Optional[str] = None) -> Dict[str, Any]:
    """Assign a staff member to a shift.

    Validates that both records exist and that the staff member is not already
    on the shift. A previously cancelled or declined assignment is reinstated
    rather than duplicated, because the schema holds a UNIQUE (shift_id,
    staff_id) constraint.
    """
    if isinstance(staff_id, bool) or not isinstance(staff_id, int):
        raise ValidationError("'staff_id' must be an integer.",
                              {"field": "staff_id", "received": staff_id})

    shift = database_client.get_shift(shift_id)      # raises NotFoundError
    staff = database_client.get_staff(staff_id)      # raises NotFoundError

    existing = [
        assignment for assignment in database_client.list_assignments(shift_id=shift_id)
        if assignment["staff_id"] == staff_id
    ]

    if existing:
        assignment = existing[0]
        if assignment["assignment_status"] not in INACTIVE_STATUSES:
            raise ConflictError(
                f"Staff {staff_id} is already assigned to shift {shift_id}.",
                {"assignment_id": assignment["assignment_id"],
                 "assignment_status": assignment["assignment_status"]},
            )
        # Reinstate the withdrawn assignment.
        return database_client.update_assignment(
            assignment["assignment_id"],
            assignment_status="Assigned",
            approved_by=approved_by,
            approved_at=_now() if approved_by else None,
        )

    return database_client.create_assignment(
        shift_id=shift_id,
        staff_id=staff_id,
        assignment_status="Assigned",
        approved_by=approved_by,
        approved_at=_now() if approved_by else None,
    )


def unassign_staff(shift_id: int, staff_id: Any) -> Dict[str, Any]:
    """Withdraw a staff member from a shift.

    Implemented as a status change to ``Cancelled`` rather than a row delete,
    which is why the endpoint is a PUT: the allocation history is retained as
    evidence of who was rostered and when that changed.
    """
    if isinstance(staff_id, bool) or not isinstance(staff_id, int):
        raise ValidationError("'staff_id' must be an integer.",
                              {"field": "staff_id", "received": staff_id})

    database_client.get_shift(shift_id)   # raises NotFoundError

    matches = [
        assignment for assignment in database_client.list_assignments(shift_id=shift_id)
        if assignment["staff_id"] == staff_id
    ]
    if not matches:
        raise NotFoundError(f"Staff {staff_id} is not assigned to shift {shift_id}.")

    assignment = matches[0]
    if assignment["assignment_status"] == "Cancelled":
        raise ConflictError(
            f"Assignment for staff {staff_id} on shift {shift_id} is already cancelled.",
            {"assignment_id": assignment["assignment_id"]},
        )

    return database_client.update_assignment(
        assignment["assignment_id"], assignment_status="Cancelled"
    )


def list_shift_assignments(shift_id: int) -> List[Dict[str, Any]]:
    """Return every assignment recorded against a shift, with staff detail."""
    database_client.get_shift(shift_id)
    return database_client.list_staff_for_shift(shift_id)
