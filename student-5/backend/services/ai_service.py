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
rostering decision is made. Nothing here assigns anyone.

Eligibility is NOT decided in this module. ``eligibility_service`` is the
single source of truth, and it is the same module the manual candidate list
uses, so the shortlist offered here can never contain someone the drawer would
refuse to assign. The model, once connected, re-orders and explains an
already-vetted list — it is never shown the ineligible and never gets a vote
on who is eligible.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from config import Config
from services import coverage_service, eligibility_service


def _llm_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """Project one eligible candidate down to what a model may be told.

    Names are withheld: ranking needs role, department, specialisation and the
    advisory availability flag, never identity, and the backend re-joins the
    staff_id to a name itself. Free-text ``notes`` on the staff record and the
    ``reason`` on an absence request are excluded outright — they can carry
    medical or personal detail that has no business in a prompt.

    ``blocked_reason`` is omitted because it is null for everything sent: only
    eligible candidates reach this function.
    """
    return {
        "staff_id": candidate["staff_id"],
        "role": candidate["role"],
        "department": candidate["department"],
        "specialisation": candidate["specialisation"],
        "employment_status": candidate["employment_status"],
        "weekly_availability_matches": candidate["weekly_ok"],
    }


def suggest_staff(shift_id: int, limit: int = 5) -> Dict[str, Any]:
    """Return the ELIGIBLE staff for a shift, ordered, with the LLM context.

    Every candidate returned has passed the deterministic hard rules in
    ``eligibility_service``. Ineligible staff are excluded outright rather than
    ranked low: a shortlist is a list of people who can actually work the
    shift, and demoting a blocked candidate still invites a manager to pick
    them. The manual candidate list, by contrast, deliberately shows blocked
    staff with their reason — that is a different question, asked in a
    different place.

    ``limit`` caps the shortlist only. ``eligible_count`` reports how many
    passed the rules, so a manager can tell a short list from a scarce one.
    """
    result = eligibility_service.eligible_candidates(shift_id)
    eligible = result["candidates"]
    shortlist = eligible[:limit]

    return {
        "ai_enabled": Config.AI_ENABLED,
        "mode": "rule-based",
        "note": ("Deterministic eligibility. LLM re-ranking is added during the "
                 "AI integration task and will re-order this same shortlist "
                 "without changing who is on it; the manager assigns."),
        "shift": result["shift"],
        "already_assigned_staff_ids": result["already_assigned_staff_ids"],
        "eligible_count": result["eligible_count"],
        "suggestions": shortlist,
        "context": {
            "task": "suggest_staff_for_shift",
            "shift": result["shift"],
            "candidate_count": len(eligible),
            "candidates": [_llm_candidate(row) for row in shortlist],
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
