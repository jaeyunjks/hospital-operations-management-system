"""Request validation helpers for the Student 5 backend/API microservice.

Validation happens at the service boundary so invalid input is rejected with a
clear 400 before it ever reaches the database microservice.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

from errors import ValidationError

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

AVAILABILITY_STATUSES = ("Available", "Unavailable", "On Leave")
# Mirrors the CHECK constraint on staff.employment_status.
EMPLOYMENT_STATUSES = ("Full-Time", "Part-Time", "Casual", "Contract")
SHIFT_STATUSES = ("Planned", "Open", "Filled", "Completed", "Cancelled")
ASSIGNMENT_STATUSES = ("Assigned", "Confirmed", "Declined", "Cancelled", "Completed")


def require_json(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reject a request that carried no JSON object."""
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")
    return payload


def require_fields(payload: Dict[str, Any], fields: Iterable[str]) -> None:
    """Reject a payload missing any required field."""
    missing = [name for name in fields if payload.get(name) in (None, "")]
    if missing:
        raise ValidationError(
            "Missing required field(s): " + ", ".join(missing),
            {"missing_fields": missing},
        )


def validate_choice(value: Any, allowed: Iterable[str], field: str) -> str:
    """Reject a value outside the permitted set for that column."""
    allowed = tuple(allowed)
    if value not in allowed:
        raise ValidationError(
            f"'{field}' must be one of: {', '.join(allowed)}.",
            {"field": field, "allowed": list(allowed), "received": value},
        )
    return value


def validate_date(value: Any, field: str = "shift_date") -> str:
    """Require an ISO 8601 date (YYYY-MM-DD)."""
    if not isinstance(value, str) or not DATE_PATTERN.match(value):
        raise ValidationError(f"'{field}' must be a date in YYYY-MM-DD format.",
                              {"field": field, "received": value})
    return value


def validate_time(value: Any, field: str) -> str:
    """Require a 24-hour time (HH:MM)."""
    if not isinstance(value, str) or not TIME_PATTERN.match(value):
        raise ValidationError(f"'{field}' must be a time in HH:MM format.",
                              {"field": field, "received": value})
    return value


def validate_positive_int(value: Any, field: str) -> int:
    """Require an integer greater than zero."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"'{field}' must be an integer.",
                              {"field": field, "received": value})
    if value < 1:
        raise ValidationError(f"'{field}' must be greater than zero.",
                              {"field": field, "received": value})
    return value


def validate_non_empty_string(value: Any, field: str) -> str:
    """Require a non-blank string."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"'{field}' must be a non-empty string.",
                              {"field": field, "received": value})
    return value.strip()


def validate_shift_payload(payload: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
    """Validate a shift create (``partial=False``) or update (``partial=True``).

    Returns only the recognised fields, so unknown keys never reach the
    database microservice.
    """
    if not partial:
        require_fields(payload, ("department", "shift_date", "start_time",
                                 "end_time", "required_role"))

    cleaned: Dict[str, Any] = {}

    if "department" in payload:
        cleaned["department"] = validate_non_empty_string(payload["department"], "department")
    if "shift_date" in payload:
        cleaned["shift_date"] = validate_date(payload["shift_date"])
    if "start_time" in payload:
        cleaned["start_time"] = validate_time(payload["start_time"], "start_time")
    if "end_time" in payload:
        cleaned["end_time"] = validate_time(payload["end_time"], "end_time")
    if "required_role" in payload:
        cleaned["required_role"] = validate_non_empty_string(payload["required_role"], "required_role")
    if "required_staff_count" in payload:
        cleaned["required_staff_count"] = validate_positive_int(
            payload["required_staff_count"], "required_staff_count")
    if "shift_status" in payload:
        cleaned["shift_status"] = validate_choice(
            payload["shift_status"], SHIFT_STATUSES, "shift_status")
    if "notes" in payload:
        cleaned["notes"] = payload["notes"]

    if partial and not cleaned:
        raise ValidationError("No updatable fields supplied.")

    if "start_time" in cleaned and "end_time" in cleaned:
        if cleaned["start_time"] == cleaned["end_time"]:
            raise ValidationError("'start_time' and 'end_time' must differ.")

    return cleaned
