"""Shift application logic for the Student 5 backend/API microservice."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database_client import database_client
from errors import ValidationError
from validation import (SHIFT_STATUSES, validate_choice, validate_date,
                        validate_shift_payload)


def list_shifts(department: Optional[str] = None, shift_date: Optional[str] = None,
                shift_status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return shifts, optionally filtered."""
    if shift_date:
        validate_date(shift_date)
    if shift_status:
        validate_choice(shift_status, SHIFT_STATUSES, "shift_status")
    return database_client.list_shifts(
        department=department, shift_date=shift_date, shift_status=shift_status
    )


def get_shift(shift_id: int) -> Dict[str, Any]:
    """Return one shift, or raise NotFoundError."""
    return database_client.get_shift(shift_id)


def staff_role_vocabulary() -> List[str]:
    """The roles staff members actually hold, sorted.

    Derived from live staff records rather than a hardcoded list: the roles a
    hospital employs are data, not a constant, and a list maintained by hand
    would drift out of step with the workforce.
    """
    return sorted({row["role"] for row in database_client.list_staff()
                   if row.get("role")})


def _validate_required_role(cleaned: Dict[str, Any]) -> None:
    """Reject a required_role no staff member holds.

    Without this the API accepts any non-empty string, so a manager can
    create a shift requiring a role nobody has — a shift that can never be
    filled, and whose permanent gap looks like a staffing shortage rather
    than the data-entry mistake it is.

    Department is deliberately NOT validated this way. A shift may legitimately
    target a department before anyone is employed there (opening a new ward),
    so restricting it to departments with existing staff would block real work.
    """
    role = cleaned.get("required_role")
    if role is None:
        return
    known = staff_role_vocabulary()
    if role not in known:
        raise ValidationError(
            f"Required role is not recognised: {role!r}.",
            {"field": "required_role", "allowed": known})


def create_shift(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and create a shift."""
    cleaned = validate_shift_payload(payload, partial=False)
    _validate_required_role(cleaned)
    return database_client.create_shift(**cleaned)


def update_shift(shift_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and apply a partial shift update."""
    cleaned = validate_shift_payload(payload, partial=True)
    _validate_required_role(cleaned)
    return database_client.update_shift(shift_id, **cleaned)


def delete_shift(shift_id: int) -> None:
    """Delete a shift. Its assignments are removed by the schema's ON DELETE CASCADE."""
    database_client.get_shift(shift_id)   # raises NotFoundError when absent
    database_client.delete_shift(shift_id)
