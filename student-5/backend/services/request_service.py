"""Temporary unavailability requests for Student 5 — Staff & Shift Management.

A request is a date-specific restriction with its own review lifecycle. It is
deliberately separate from the three availability concepts already modelled:

    staff.availability_status     current operational scheduling status
    staff_weekly_availability     recurring weekly pattern
    shift_assignment              actual allocation to a real shift

Approving a request NEVER mutates any of them. In particular it does not set
availability_status to 'On Leave' — a future absence is not a statement about
today — and it never adds or removes an assignment.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from database_client import database_client
from errors import ConflictError, NotFoundError, ValidationError
from validation import (validate_request_payload, validate_request_transition,
                        validate_choice, REQUEST_STATUSES)

#: Assignment states that no longer occupy a place on a shift.
_INACTIVE_ASSIGNMENT = ("Cancelled", "Declined")

#: Only an Approved request restricts scheduling. Pending, Rejected and
#: Cancelled requests must never block assignment.
_BLOCKING_STATUS = "Approved"


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def list_requests(staff_id: Optional[int] = None,
                  request_status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return requests, optionally filtered by employee and/or status."""
    if request_status:
        validate_choice(request_status, REQUEST_STATUSES, "request_status")
    if staff_id is not None:
        database_client.get_staff(staff_id)      # raises NotFoundError
    return database_client.list_unavailability_requests(
        staff_id=staff_id, request_status=request_status)


def get_request(request_id: int) -> Dict[str, Any]:
    """Return one request, or raise NotFoundError."""
    return database_client.get_unavailability_request(request_id)


def create_request(staff_id: int, payload: Any) -> Dict[str, Any]:
    """Create a Pending request for one employee.

    Rejects a range that overlaps an existing Pending or Approved request for
    the same employee. Rejected and Cancelled requests never block, so an
    employee can re-request a period that was previously declined.
    """
    database_client.get_staff(staff_id)          # 404 before validation work
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    cleaned = validate_request_payload(payload)

    clashes = database_client.list_overlapping_requests(
        staff_id, cleaned["start_date"], cleaned["end_date"])
    if clashes:
        existing = clashes[0]
        raise ConflictError(
            "This period overlaps an existing "
            f"{existing['request_status']} request "
            f"({existing['start_date']} to {existing['end_date']}).",
            {"conflicting_request_id": existing["request_id"]})

    return database_client.create_unavailability_request(staff_id=staff_id, **cleaned)


def review_request(request_id: int, decision: str,
                   reviewed_by: Optional[str] = None) -> Dict[str, Any]:
    """Manager approves or rejects a Pending request.

    Approving records the decision only. Affected roster assignments are
    derived and returned for the manager to act on; nothing is unassigned.
    """
    record = database_client.get_unavailability_request(request_id)
    validate_choice(decision, ("Approved", "Rejected"), "decision")
    validate_request_transition(record["request_status"], decision)

    if not reviewed_by or not str(reviewed_by).strip():
        raise ValidationError("'reviewed_by' is required when reviewing a request.")

    return database_client.update_unavailability_request(
        request_id, request_status=decision,
        reviewed_by=str(reviewed_by).strip(), reviewed_at=_now())


def cancel_request(request_id: int) -> Dict[str, Any]:
    """Employee cancels their own Pending request.

    reviewed_by/reviewed_at stay NULL: a cancellation is not a manager review,
    and recording one would misrepresent who acted.
    """
    record = database_client.get_unavailability_request(request_id)
    validate_request_transition(record["request_status"], "Cancelled")
    return database_client.update_unavailability_request(
        request_id, request_status="Cancelled", reviewed_by=None, reviewed_at=None)


def affected_assignments(request_id: int) -> List[Dict[str, Any]]:
    """Roster assignments that fall inside a request's date range.

    Derived on every call from the request dates plus real shift assignments —
    there is no conflict table. Cancelled and declined assignments are
    excluded because they no longer occupy a place on the shift.
    """
    record = database_client.get_unavailability_request(request_id)
    shifts = database_client.list_shifts_for_staff(record["staff_id"])
    return [
        row for row in shifts
        if row.get("assignment_status") not in _INACTIVE_ASSIGNMENT
        and record["start_date"] <= row.get("shift_date", "") <= record["end_date"]
    ]


def blocking_requests_for_date(staff_id: int, shift_date: str) -> List[Dict[str, Any]]:
    """Approved requests covering a given date, for candidate eligibility.

    Only Approved requests restrict scheduling.
    """
    if not shift_date:
        return []
    return [
        row for row in database_client.list_unavailability_requests(
            staff_id=staff_id, request_status=_BLOCKING_STATUS)
        if row["start_date"] <= shift_date <= row["end_date"]
    ]
