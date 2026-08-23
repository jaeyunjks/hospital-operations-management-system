"""AI preparation layer for the Student 5 backend/API microservice.

Release 0 scope: assemble the structured context an LLM will need, and expose
it through stable endpoints. **No LLM call is made here yet.** Ollama and the
approved open-source model are wired in during the AI integration task
(prompt artefact S5-AI-001).

Each function returns:

* ``ai_enabled``  — false until AI-Mode is switched on
* ``mode``        — "rule-based" for now, "llm" once Ollama is connected
* ``context``     — the payload that will be handed to the model
* a deterministic, rule-based result so the frontend has something to render
  in the meantime

Recommendations are decision support only and require human review before any
rostering decision is made.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import Config
from database_client import database_client
from services import assignment_service, coverage_service

#: Reason codes explaining why a candidate was ranked as they were.
REASON_AVAILABLE = "Marked Available"
REASON_ROLE_MATCH = "Role matches shift requirement"
REASON_DEPARTMENT_MATCH = "Already works in this department"
REASON_NO_CLASH = "No other assignment on this date"


def _score_candidate(staff: Dict[str, Any], shift: Dict[str, Any],
                     same_day_shift_ids: set) -> Dict[str, Any]:
    """Score one staff member against one shift, recording why."""
    reasons: List[str] = []
    score = 0

    if staff["availability_status"] == "Available":
        score += 3
        reasons.append(REASON_AVAILABLE)
    if staff["role"] == shift["required_role"]:
        score += 3
        reasons.append(REASON_ROLE_MATCH)
    if staff["department"] == shift["department"]:
        score += 2
        reasons.append(REASON_DEPARTMENT_MATCH)
    if staff["staff_id"] not in same_day_shift_ids:
        score += 1
        reasons.append(REASON_NO_CLASH)

    return {
        "staff_id": staff["staff_id"],
        "name": staff["name"],
        "role": staff["role"],
        "department": staff["department"],
        "specialisation": staff.get("specialisation"),
        "availability_status": staff["availability_status"],
        "score": score,
        "reasons": reasons,
    }


def suggest_staff(shift_id: int, limit: int = 5) -> Dict[str, Any]:
    """Return ranked candidate staff for a shift, with the LLM context prepared.

    The ranking below is a transparent rule-based shortlist. When AI-Mode is
    enabled this same ``context`` block is sent to the model, which will
    re-rank and explain the recommendation.
    """
    shift = database_client.get_shift(shift_id)                 # raises NotFoundError
    already_assigned = {
        row["staff_id"] for row in assignment_service.active_assignments(shift_id)
    }

    # Staff already rostered elsewhere on the same date.
    same_day = set()
    for other in database_client.list_shifts(shift_date=shift["shift_date"]):
        if other["shift_id"] == shift_id:
            continue
        for row in assignment_service.active_assignments(other["shift_id"]):
            same_day.add(row["staff_id"])

    candidates = [
        _score_candidate(staff, shift, same_day)
        for staff in database_client.list_staff()
        if staff["staff_id"] not in already_assigned
        and staff["availability_status"] != "Unavailable"
    ]
    candidates.sort(key=lambda candidate: (-candidate["score"], candidate["name"]))
    shortlist = candidates[:limit]

    return {
        "ai_enabled": Config.AI_ENABLED,
        "mode": "rule-based",
        "note": ("Rule-based shortlist. LLM re-ranking is added during the AI "
                 "integration task; recommendations require human review."),
        "shift": shift,
        "already_assigned_staff_ids": sorted(already_assigned),
        "suggestions": shortlist,
        "context": {
            "task": "suggest_staff_for_shift",
            "shift": shift,
            "candidate_count": len(candidates),
            "candidates": shortlist,
            "model": Config.OLLAMA_MODEL,
        },
    }


def coverage_summary(department: Optional[str] = None,
                     shift_date: Optional[str] = None) -> Dict[str, Any]:
    """Return coverage data shaped for LLM summarisation.

    Produces a deterministic plain-text summary now, and carries the structured
    ``context`` the model will summarise once AI-Mode is enabled.
    """
    coverage = coverage_service.shift_coverage(
        department=department, shift_date=shift_date
    )
    summary = coverage["summary"]
    gaps = [row for row in coverage["shifts"] if row["shortfall"] > 0]

    if summary["total_shifts"] == 0:
        headline = "No shifts match the requested filters."
    elif summary["total_shortfall"] == 0:
        headline = (f"All {summary['total_shifts']} shift(s) are fully staffed.")
    else:
        headline = (
            f"{summary['understaffed']} of {summary['total_shifts']} shift(s) are "
            f"short by {summary['total_shortfall']} staff member(s) in total."
        )

    return {
        "ai_enabled": Config.AI_ENABLED,
        "mode": "rule-based",
        "note": ("Deterministic summary. LLM narrative is added during the AI "
                 "integration task; output requires human review."),
        "headline": headline,
        "summary": summary,
        "gaps": gaps,
        "context": {
            "task": "summarise_staffing_coverage",
            "filters": coverage["filters"],
            "summary": summary,
            "understaffed_shifts": gaps,
            "model": Config.OLLAMA_MODEL,
        },
    }
