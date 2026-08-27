"""Staff application logic for the Student 5 backend/API microservice."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database_client import database_client
from errors import ValidationError
from validation import (AVAILABILITY_STATUSES, EMPLOYMENT_STATUSES,
                        validate_choice, validate_weekly_availability)


def list_staff(department: Optional[str] = None, role: Optional[str] = None,
               availability_status: Optional[str] = None,
               employment_status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return staff records, optionally filtered."""
    if availability_status:
        validate_choice(availability_status, AVAILABILITY_STATUSES, "availability_status")
    if employment_status:
        validate_choice(employment_status, EMPLOYMENT_STATUSES, "employment_status")
    return database_client.list_staff(
        department=department, role=role,
        availability_status=availability_status,
        employment_status=employment_status,
    )


def get_staff(staff_id: int) -> Dict[str, Any]:
    """Return one staff record, or raise NotFoundError."""
    return database_client.get_staff(staff_id)


def list_staff_shifts(staff_id: int) -> List[Dict[str, Any]]:
    """Return the shifts this staff member is assigned to.

    Exposes the existing STAFF 1:M SHIFT_ASSIGNMENT M:1 SHIFT relationship;
    the repository and database service already provided this query.
    """
    database_client.get_staff(staff_id)   # raises NotFoundError when absent
    return database_client.list_shifts_for_staff(staff_id)


def update_availability(staff_id: int, availability_status: Any) -> Dict[str, Any]:
    """Update one staff member's availability status."""
    validate_choice(availability_status, AVAILABILITY_STATUSES, "availability_status")
    return database_client.update_staff(staff_id, availability_status=availability_status)


def search_staff(query: Optional[str] = None, department: Optional[str] = None,
                 role: Optional[str] = None,
                 availability_status: Optional[str] = None,
                 employment_status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Search staff by free-text term across name, role, department, specialisation.

    Structured filters are applied by the database service; the free-text match
    is applied here, keeping the data service free of application concerns.
    """
    if query is not None and not query.strip():
        raise ValidationError("Search term 'q' must not be blank.")

    records = list_staff(department=department, role=role,
                         availability_status=availability_status,
                         employment_status=employment_status)

    if not query:
        return records

    term = query.strip().lower()
    searchable = ("name", "role", "department", "specialisation")
    return [
        record for record in records
        if any(term in str(record.get(field) or "").lower() for field in searchable)
    ]


def get_weekly_availability(staff_id: int) -> List[Dict[str, Any]]:
    """Return the recurring weekly availability owned by HOMS.

    Separate from `availability_status` (current operational scheduling
    status) and from shift assignments (actual allocation).
    """
    database_client.get_staff(staff_id)      # raises NotFoundError when absent
    return database_client.list_weekly_availability(staff_id)


def replace_weekly_availability(staff_id: int, periods: Any) -> List[Dict[str, Any]]:
    """Validate and replace a staff member's whole weekly pattern."""
    database_client.get_staff(staff_id)      # 404 before any validation work
    cleaned = validate_weekly_availability(periods)
    return database_client.replace_weekly_availability(staff_id, cleaned)
