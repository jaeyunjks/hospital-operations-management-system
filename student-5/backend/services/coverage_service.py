"""Staffing coverage analysis for the Student 5 backend/API microservice.

Coverage compares the number of staff a shift requires against the number
currently assigned, so administrators can see where the roster is short.

SINGLE SOURCE OF TRUTH for coverage arithmetic. Every derived quantity — what
is filled, what is short, what is spare, and the resulting percentage — is
computed here once and consumed by the frontend and, later, by the AI summary.
Neither re-derives it, so a KPI tile and a generated narrative cannot report
different numbers for the same roster.

THE RULE THAT MATTERS: gap and surplus are computed PER SHIFT and only then
summed. Netting the totals first would let extra staff on one shift cancel a
shortage on another — A(required 2, assigned 3) plus B(required 2, assigned 1)
would report no gap at all, hiding a real shortage behind an average. For the
same reason coverage counts FILLED positions, ``min(assigned, required)``, so
an overstaffed shift can never push the percentage past 100% or mask a gap
elsewhere. Surplus is reported separately rather than folded in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from database_client import database_client
from services import assignment_service


#: Coverage status labels. "Unstaffed" is the subset of understaffed shifts
#: with nobody at all on them, so it is reported as its own count while still
#: counting towards the shortfall.
STATUS_UNSTAFFED = "Unstaffed"
STATUS_UNDERSTAFFED = "Understaffed"
STATUS_OVERSTAFFED = "Overstaffed"
STATUS_FULLY_STAFFED = "Fully staffed"


def _classify(assigned: int, required: int) -> str:
    if assigned == 0 and required > 0:
        return STATUS_UNSTAFFED
    if assigned < required:
        return STATUS_UNDERSTAFFED
    if assigned > required:
        return STATUS_OVERSTAFFED
    return STATUS_FULLY_STAFFED


def coverage_percentage(filled: int, required: int) -> Optional[int]:
    """Filled positions as a whole percentage of required, or None.

    ``None`` rather than 0 or 100 when nothing is required: a day with no
    shifts has no coverage to report, and either number would be a claim about
    a roster that does not exist. The UI renders this as an em dash.

    The cap at 100% is structural rather than clamped. ``filled`` is already
    the per-shift ``min(assigned, required)``, so it can never exceed
    ``required`` and the ratio cannot exceed 1 — the only way past 100% would
    be to count surplus staff as coverage, which is exactly what this
    arithmetic refuses to do.
    """
    if not required:
        return None
    return round(filled / required * 100)


def shift_totals(required: Any, assigned: Any) -> Dict[str, int]:
    """The three derived quantities for ONE shift.

    Kept as a named function because these three lines are the whole of the
    coverage rule, and every total in the system is a sum of them.
    """
    required = int(required)
    assigned = int(assigned)
    return {
        "filled_staff_count": min(assigned, required),
        "shortfall": max(required - assigned, 0),
        "surplus": max(assigned - required, 0),
    }


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
            **shift_totals(required, assigned),
            "coverage_status": _classify(assigned, required),
        })

    understaffed = [row for row in breakdown if row["shortfall"] > 0]

    # Every total below is a sum over per-shift figures that were already
    # floored at zero, which is what stops one shift's surplus cancelling
    # another's shortage.
    required_positions = sum(row["required_staff_count"] for row in breakdown)
    filled_positions = sum(row["filled_staff_count"] for row in breakdown)

    return {
        "filters": {
            "department": department,
            "shift_date": shift_date,
            "shift_status": shift_status,
        },
        "summary": {
            "total_shifts": len(breakdown),
            "fully_staffed": sum(1 for row in breakdown
                                 if row["coverage_status"] == STATUS_FULLY_STAFFED),
            # Counts every shift carrying a gap, INCLUDING the unstaffed ones
            # reported separately below. Pre-existing semantics, unchanged.
            "understaffed": len(understaffed),
            "unstaffed": sum(1 for row in breakdown
                             if row["coverage_status"] == STATUS_UNSTAFFED),
            "overstaffed": sum(1 for row in breakdown
                               if row["coverage_status"] == STATUS_OVERSTAFFED),
            "total_shortfall": sum(row["shortfall"] for row in breakdown),
            "total_surplus": sum(row["surplus"] for row in breakdown),
            "required_positions": required_positions,
            "assigned_positions": sum(row["assigned_staff_count"] for row in breakdown),
            "filled_positions": filled_positions,
            "coverage_pct": coverage_percentage(filled_positions, required_positions),
        },
        "shifts": breakdown,
    }
