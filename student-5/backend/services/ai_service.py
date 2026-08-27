"""AI preparation layer for the Student 5 backend/API microservice.

``suggest_staff`` ranks with Ollama when AI-Mode is on; ``coverage_summary``
is still structure-only and awaits its own integration task.

The pipeline is strictly ordered, and the order is the safety property:

    eligibility_service  ->  eligible candidates ONLY  ->  optional Ollama
    ranking  ->  Staff Manager assigns manually

The model is handed a list that is already correct and asked to order it. It
cannot add a candidate, remove one, or make an ineligible one eligible — not
because it is asked not to, but because ``_merge_ranking`` discards every id
that was not in the input set and fills any gaps from the deterministic order.
Nothing here assigns anyone.

Each function returns:

* ``ai_enabled``  — whether AI-Mode is switched on at all
* ``mode``        — "ai" when the model ranked the list, "rule-based" otherwise
* ``ranking``     — the source, the model, and why a fallback happened
* ``context``     — the payload that was (or would be) handed to the model
* a deterministic result in every case, so a missing or broken model costs
  the rationales and the ordering, never the shortlist

Eligibility is NOT decided in this module. ``eligibility_service`` is the
single source of truth, and it is the same module the manual candidate list
uses, so the shortlist offered here can never contain someone the drawer would
refuse to assign.

Recommendations are decision support only and require human review before any
rostering decision is made.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import Config
from prompts import suggest_staff as suggest_staff_prompt
from services import coverage_service, eligibility_service
from services.ollama_client import (REASON_INVALID_OUTPUT, REASON_UNAVAILABLE,
                                    OllamaError, ollama_client)

#: AI-Mode is switched off, so no call was attempted.
FALLBACK_AI_DISABLED = "ai_disabled"

#: Nothing passed the eligibility rules, so there was nothing to rank.
FALLBACK_NO_CANDIDATES = "no_candidates"

#: Plain-English explanation per fallback code. These reach the API and the
#: manager, so they say what happened to the ranking — never which exception
#: was raised, which host refused, or what the model actually returned.
_FALLBACK_NOTES = {
    FALLBACK_AI_DISABLED: (
        "Deterministic ordering. AI-Mode is switched off."),
    FALLBACK_NO_CANDIDATES: (
        "No staff are eligible for this shift, so no ranking was requested."),
    REASON_UNAVAILABLE: (
        "Deterministic ordering. The ranking model could not be reached."),
    REASON_INVALID_OUTPUT: (
        "Deterministic ordering. The ranking model returned an unusable "
        "response."),
}

#: A rationale is a caption, not a report. Model output is untrusted text, so
#: it is flattened to a single line and capped before it can reach a template.
_MAX_RATIONALE = 120


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


def _llm_shift(shift: Dict[str, Any]) -> Dict[str, Any]:
    """Project the shift down to what a model needs in order to rank.

    ``notes`` is dropped along with the identifiers and timestamps. A shift
    note is free text a manager typed, and free text is exactly where clinical
    or personal detail ends up — it has no bearing on which of two eligible
    nurses should be offered first.
    """
    return {
        "department": shift.get("department"),
        "shift_date": shift.get("shift_date"),
        "start_time": shift.get("start_time"),
        "end_time": shift.get("end_time"),
        "required_role": shift.get("required_role"),
        "required_staff_count": shift.get("required_staff_count"),
    }


def _clean_rationale(value: Any) -> Optional[str]:
    """Flatten one model-written rationale to a short single-line caption."""
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None
    return text[:_MAX_RATIONALE].rstrip()


def _ranked_staff_id(entry: Any) -> Optional[int]:
    """The staff_id in one ranking entry, or None if there isn't a usable one.

    Accepts the documented ``{"staff_id": 3, ...}`` and a bare ``3``, because a
    smaller model will occasionally return the shorter form and rejecting the
    whole ranking over it would cost the manager their rationales for nothing.
    A string of digits is accepted for the same reason; booleans are not, since
    ``True`` would otherwise pass as staff 1.
    """
    raw = entry.get("staff_id") if isinstance(entry, dict) else entry
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    return None


def _merge_ranking(shortlist: List[Dict[str, Any]],
                   ranking: Any) -> List[Dict[str, Any]]:
    """Apply a model ranking to the eligible shortlist, defensively.

    This is where the model's answer stops being trusted input and becomes a
    suggestion about ORDER only:

    * an id that was not in the shortlist is discarded — the model cannot
      introduce a candidate, invented or merely ineligible
    * a repeated id is taken once — the model cannot clone anyone into the
      list twice
    * an eligible candidate the model omitted is appended in the deterministic
      order it already had — the model cannot drop anyone by staying silent

    Raises ``OllamaError`` when the reply yields no usable id at all. Falling
    through would produce the deterministic order under a "ranked by AI" label,
    which is worse than an honest fallback: it claims a provenance the ordering
    does not have.
    """
    if not isinstance(ranking, list):
        raise OllamaError(REASON_INVALID_OUTPUT, "ranking was not a list")

    by_id = {candidate["staff_id"]: candidate for candidate in shortlist}
    ordered: List[Dict[str, Any]] = []
    seen = set()

    for entry in ranking:
        staff_id = _ranked_staff_id(entry)
        if staff_id is None or staff_id not in by_id or staff_id in seen:
            continue
        seen.add(staff_id)
        candidate = dict(by_id[staff_id])
        rationale = _clean_rationale(
            entry.get("rationale") if isinstance(entry, dict) else None)
        if rationale:
            candidate["rationale"] = rationale
        ordered.append(candidate)

    if not ordered:
        raise OllamaError(REASON_INVALID_OUTPUT, "no known staff_id in ranking")

    # Whoever the model left out keeps their deterministic position, after
    # everyone it did rank.
    ordered.extend(dict(candidate) for candidate in shortlist
                   if candidate["staff_id"] not in seen)
    return ordered


def _rank_with_model(shift: Dict[str, Any],
                     shortlist: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ask the model to order the shortlist. Raises ``OllamaError`` on any miss."""
    reply = ollama_client.generate_json(
        prompt=suggest_staff_prompt.build_prompt(
            _llm_shift(shift), [_llm_candidate(row) for row in shortlist]),
        system=suggest_staff_prompt.SYSTEM_PROMPT,
    )
    return _merge_ranking(
        shortlist, reply.get(suggest_staff_prompt.RANKING_KEY))


def suggest_staff(shift_id: int, limit: int = 5) -> Dict[str, Any]:
    """Return the ELIGIBLE staff for a shift, ranked, with the LLM context.

    Deterministic eligibility runs first and unconditionally. Ranking is an
    optional layer on top: when AI-Mode is on and there is something to rank,
    the shortlist goes to Ollama and comes back reordered with a rationale per
    candidate. Every failure — off, unreachable, slow, or unusable output —
    lands on the same deterministic ordering the caller would have got anyway,
    with ``mode`` and ``ranking.fallback_reason`` saying so plainly.

    The model ranks the shortlist AFTER ``limit`` has been applied, not the
    full eligible set. Capping first keeps the model to reordering the same
    people a manager would already have seen; letting it rank first would let
    it decide who appears at all, which is a larger delegation than this
    feature intends.

    ``limit`` caps the shortlist only. ``eligible_count`` reports how many
    passed the rules, so a manager can tell a short list from a scarce one.
    Nothing here assigns anyone: the manager still picks from the list.
    """
    result = eligibility_service.eligible_candidates(shift_id)
    eligible = result["candidates"]
    shortlist = [dict(candidate) for candidate in eligible[:limit]]

    suggestions = shortlist
    mode = "rule-based"
    fallback_reason: Optional[str] = None

    if not Config.AI_ENABLED:
        fallback_reason = FALLBACK_AI_DISABLED
    elif not shortlist:
        # Nothing to rank. Calling the model to sort an empty list would spend
        # a timeout budget to learn what is already known.
        fallback_reason = FALLBACK_NO_CANDIDATES
    else:
        try:
            suggestions = _rank_with_model(result["shift"], shortlist)
            mode = "ai"
        except OllamaError as error:
            # The reason CODE is kept; the exception detail is not. A manager
            # needs to know the ranking is deterministic, not why a socket
            # failed, and an internal message is not theirs to read.
            fallback_reason = error.reason

    note = (_FALLBACK_NOTES.get(fallback_reason, _FALLBACK_NOTES[REASON_UNAVAILABLE])
            if fallback_reason is not None
            else ("Ranked by the local model from the eligible candidates only. "
                  "A recommendation is not an assignment; the manager decides."))

    return {
        "ai_enabled": Config.AI_ENABLED,
        "mode": mode,
        "note": note,
        "ranking": {
            "source": "ollama" if mode == "ai" else "deterministic",
            "model": Config.OLLAMA_MODEL,
            "fallback_reason": fallback_reason,
        },
        "shift": result["shift"],
        "already_assigned_staff_ids": result["already_assigned_staff_ids"],
        "eligible_count": result["eligible_count"],
        "suggestions": suggestions,
        "context": {
            "task": "suggest_staff_for_shift",
            "shift": _llm_shift(result["shift"]),
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
