"""Request validation helpers for the Student 5 backend/API microservice.

Validation happens at the service boundary so invalid input is rejected with a
clear 400 before it ever reaches the database microservice.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

from errors import ConflictError, ValidationError

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


# ==========================================================================
# Weekly availability
# ==========================================================================
DAYS_OF_WEEK = ("Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday")

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY


def _to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def weekly_period_segments(day_of_week: int, start_time: str, end_time: str):
    """Map one weekly period onto absolute minute segments in a 7-day week.

    An overnight period (``end_time <= start_time``) wraps into the following
    day, and a Sunday-night period wraps around to Monday — so a period can
    produce two segments. Returning segments lets overlap be tested with plain
    linear comparisons instead of modular arithmetic at every call site.
    """
    start = day_of_week * MINUTES_PER_DAY + _to_minutes(start_time)
    length = (_to_minutes(end_time) - _to_minutes(start_time)) % MINUTES_PER_DAY
    if length == 0:                       # guarded earlier; belt and braces
        length = MINUTES_PER_DAY
    end = start + length

    if end <= MINUTES_PER_WEEK:
        return [(start, end)]
    # Wraps past Sunday midnight back into Monday.
    return [(start, MINUTES_PER_WEEK), (0, end - MINUTES_PER_WEEK)]


def _segments_overlap(first, second) -> bool:
    return any(a_start < b_end and b_start < a_end
               for a_start, a_end in first
               for b_start, b_end in second)


def validate_weekly_availability(periods: Any) -> List[Dict[str, Any]]:
    """Validate a complete weekly availability pattern.

    The model is sparse: every submitted period IS an available period, so no
    state field is accepted. Rejects malformed days/times, zero-length
    periods, and any pair of OVERLAPPING intervals — not merely exact
    duplicates — including overnight and week-boundary wraps.
    """
    if not isinstance(periods, list):
        raise ValidationError("'periods' must be a list.")

    cleaned: List[Dict[str, Any]] = []
    segments: List[Any] = []

    for index, period in enumerate(periods):
        if not isinstance(period, dict):
            raise ValidationError(f"Period {index} must be an object.")

        day = period.get("day_of_week")
        if isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6:
            raise ValidationError(
                "'day_of_week' must be an integer from 0 (Monday) to 6 (Sunday).",
                {"index": index, "received": day})

        start = validate_time(period.get("start_time"), "start_time")
        end = validate_time(period.get("end_time"), "end_time")
        if start == end:
            raise ValidationError(
                "'start_time' and 'end_time' must differ.", {"index": index})

        current = weekly_period_segments(day, start, end)
        for position, existing in enumerate(segments):
            if _segments_overlap(current, existing):
                raise ValidationError(
                    "Availability periods must not overlap.",
                    {"index": index, "overlaps_index": position})
        segments.append(current)

        entry = {"day_of_week": day, "start_time": start, "end_time": end}
        notes = period.get("notes")
        if notes:
            entry["notes"] = validate_non_empty_string(notes, "notes")
        cleaned.append(entry)

    return cleaned


# ==========================================================================
# Unavailability requests
# ==========================================================================
REQUEST_STATUSES = ("Pending", "Approved", "Rejected", "Cancelled")

#: Release 0 lifecycle is one-way: only a Pending request may transition, and
#: the terminal states are final. No reopening or editing.
REQUEST_TRANSITIONS = {
    "Pending": ("Approved", "Rejected", "Cancelled"),
    "Approved": (),
    "Rejected": (),
    "Cancelled": (),
}


def validate_request_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a new unavailability request."""
    require_fields(payload, ("start_date", "end_date", "reason"))
    start = validate_date(payload["start_date"], "start_date")
    end = validate_date(payload["end_date"], "end_date")
    if start > end:
        raise ValidationError("'start_date' must be on or before 'end_date'.",
                              {"start_date": start, "end_date": end})

    cleaned = {
        "start_date": start,
        "end_date": end,
        "reason": validate_non_empty_string(payload["reason"], "reason"),
    }
    notes = payload.get("notes")
    cleaned["notes"] = validate_non_empty_string(notes, "notes") if notes else None
    return cleaned


def validate_request_transition(current_status: str, new_status: str) -> str:
    """Reject any transition the one-way Release 0 lifecycle disallows."""
    validate_choice(new_status, REQUEST_STATUSES, "request_status")
    allowed = REQUEST_TRANSITIONS.get(current_status, ())
    if new_status not in allowed:
        # Phrased without an article: "A Approved request" is wrong, and
        # picking a/an per status is more trouble than the sentence is worth.
        raise ConflictError(
            f"This request is {current_status} and cannot become {new_status}. "
            f"Decisions in Release 0 are final.",
            {"current_status": current_status, "requested_status": new_status,
             "allowed": list(allowed)})
    return new_status
