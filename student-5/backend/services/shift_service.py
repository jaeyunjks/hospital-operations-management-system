"""Shift application logic for the Student 5 backend/API microservice."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database_client import database_client
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


def create_shift(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and create a shift."""
    return database_client.create_shift(**validate_shift_payload(payload, partial=False))


def update_shift(shift_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and apply a partial shift update."""
    return database_client.update_shift(
        shift_id, **validate_shift_payload(payload, partial=True)
    )


def delete_shift(shift_id: int) -> None:
    """Delete a shift. Its assignments are removed by the schema's ON DELETE CASCADE."""
    database_client.get_shift(shift_id)   # raises NotFoundError when absent
    database_client.delete_shift(shift_id)
