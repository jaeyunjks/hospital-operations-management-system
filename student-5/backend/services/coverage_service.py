"""Staffing coverage analysis for the Student 5 backend/API microservice.

Coverage compares the number of staff a shift requires against the number
currently assigned, so administrators can see where the roster is short.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database_client import database_client
from services import assignment_service


def _classify(assigned: int, required: int) -> str:
    if assigned == 0 and required > 0:
        return "Unstaffed"
    if assigned < required:
        return "Understaffed"
    if assigned > required:
        return "Overstaffed"
    return "Fully staffed"


def shift_coverage(department: Optional[str] = None, shift_date: Optional[str] = None,
                   shift_status: Optional[str] = None) -> Dict[str, Any]:
    """Return per-shift coverage plus a roster-wide summary."""
    shifts = database_client.list_shifts(
        department=department, shift_date=shift_date, shift_status=shift_status
    )

    breakdown: List[Dict[str, Any]] = []
    for shift in shifts:
        required = int(shift["required_staff_count"])
        assigned = len(assignment_service.active_assignments(shift["shift_id"]))
        breakdown.append({
            "shift_id": shift["shift_id"],
            "department": shift["department"],
            "shift_date": shift["shift_date"],
            "start_time": shift["start_time"],
            "end_time": shift["end_time"],
            "required_role": shift["required_role"],
            "required_staff_count": required,
            "assigned_staff_count": assigned,
            "shortfall": max(required - assigned, 0),
            "coverage_status": _classify(assigned, required),
        })

    understaffed = [row for row in breakdown if row["shortfall"] > 0]

    return {
        "filters": {
            "department": department,
            "shift_date": shift_date,
            "shift_status": shift_status,
        },
        "summary": {
            "total_shifts": len(breakdown),
            "fully_staffed": sum(1 for row in breakdown
                                 if row["coverage_status"] == "Fully staffed"),
            "understaffed": len(understaffed),
            "unstaffed": sum(1 for row in breakdown
                             if row["coverage_status"] == "Unstaffed"),
            "total_shortfall": sum(row["shortfall"] for row in breakdown),
        },
        "shifts": breakdown,
    }
