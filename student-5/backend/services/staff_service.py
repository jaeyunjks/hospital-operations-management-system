"""Staff application logic for the Student 5 backend/API microservice."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database_client import database_client
from errors import ValidationError
from validation import AVAILABILITY_STATUSES, validate_choice


def list_staff(department: Optional[str] = None, role: Optional[str] = None,
               availability_status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return staff records, optionally filtered."""
    if availability_status:
        validate_choice(availability_status, AVAILABILITY_STATUSES, "availability_status")
    return database_client.list_staff(
        department=department, role=role, availability_status=availability_status
    )


def update_availability(staff_id: int, availability_status: Any) -> Dict[str, Any]:
    """Update one staff member's availability status."""
    validate_choice(availability_status, AVAILABILITY_STATUSES, "availability_status")
    return database_client.update_staff(staff_id, availability_status=availability_status)


def search_staff(query: Optional[str] = None, department: Optional[str] = None,
                 role: Optional[str] = None,
                 availability_status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Search staff by free-text term across name, role, department, specialisation.

    Structured filters are applied by the database service; the free-text match
    is applied here, keeping the data service free of application concerns.
    """
    if query is not None and not query.strip():
        raise ValidationError("Search term 'q' must not be blank.")

    records = list_staff(department=department, role=role,
                         availability_status=availability_status)

    if not query:
        return records

    term = query.strip().lower()
    searchable = ("name", "role", "department", "specialisation")
    return [
        record for record in records
        if any(term in str(record.get(field) or "").lower() for field in searchable)
    ]
