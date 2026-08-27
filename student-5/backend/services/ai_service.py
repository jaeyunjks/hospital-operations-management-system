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
from prompts import coverage_summary as coverage_summary_prompt
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

#: Narration was never asked for. Not a failure — the deterministic summary
#: loads on every page view, and only an explicit request calls the model.
FALLBACK_NOT_REQUESTED = "not_requested"

#: No shift matched the filters, so there was nothing to narrate.
FALLBACK_NO_SHIFTS = "no_shifts"

#: The narrative contained a staffing figure the facts do not support. Kept
#: distinct from ``invalid_model_output``: unusable JSON is a broken model,
#: whereas this is a model that answered fluently and got the numbers wrong,
#: which is the more dangerous of the two and worth naming.
FALLBACK_UNSUPPORTED_NUMBERS = "unsupported_numbers"

#: Coverage-summary equivalents of ``_FALLBACK_NOTES``. Separate because the
#: wording describes a narrative, not an ordering.
_COVERAGE_FALLBACK_NOTES = {
    FALLBACK_NOT_REQUESTED: (
        "Deterministic summary. AI narration was not requested."),
    FALLBACK_AI_DISABLED: (
        "Deterministic summary. AI-Mode is switched off."),
    FALLBACK_NO_SHIFTS: (
        "No shifts match the requested filters, so there was nothing to "
        "summarise."),
    REASON_UNAVAILABLE: (
        "Deterministic summary. The summarisation model could not be reached."),
    REASON_INVALID_OUTPUT: (
        "Deterministic summary. The summarisation model returned an unusable "
        "response."),
    FALLBACK_UNSUPPORTED_NUMBERS: (
        "Deterministic summary. The generated narrative contained staffing "
        "figures the roster does not support, so it was discarded."),
}

#: A rationale is a caption, not a report. Model output is untrusted text, so
#: it is flattened to a single line and capped before it can reach a template.
_MAX_RATIONALE = 120

#: A coverage narrative is a short paragraph; its priorities are captions.
_MAX_SUMMARY = 600
_MAX_PRIORITY = 120
_MAX_PRIORITIES = 5

#: At most this many shifts are described to the model. Ordered worst-first,
#: so a large roster is truncated from the fully staffed end where the detail
#: matters least; the totals still describe every shift.
_MAX_FACT_SHIFTS = 20

#: Number words the validator understands, so "three nurses short" is checked
#: as carefully as "3 nurses short". Beyond twelve a model writes digits.
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}


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


def _clean_text(value: Any, limit: int) -> Optional[str]:
    """Flatten one piece of model-written text to a bounded single line.

    Model output is untrusted text on its way to a template: collapsing the
    whitespace and capping the length stops it arriving as a wall of prose or
    smuggling layout through newlines.
    """
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    if not text:
        return None
    return text[:limit].rstrip()


def _clean_rationale(value: Any) -> Optional[str]:
    """Flatten one model-written rationale to a short single-line caption."""
    return _clean_text(value, _MAX_RATIONALE)


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


# ---------------------------------------------------- coverage narration
def _coverage_fact_shift(row: Dict[str, Any]) -> Dict[str, Any]:
    """Project one coverage row down to what a model may be told.

    Every field here is an aggregate count or a scheduling attribute. What is
    absent is the point: no ``shift_id`` (the narrative names departments and
    times, not internal keys), and no free text of any kind. Shift notes,
    staff notes and absence reasons never enter this projection, because that
    is where clinical and personal detail lives and none of it bears on
    describing a staffing position.
    """
    return {
        "department": row["department"],
        "shift_date": row["shift_date"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "required_role": row["required_role"],
        "required_positions": row["required_staff_count"],
        "assigned_positions": row["assigned_staff_count"],
        "filled_positions": row["filled_staff_count"],
        "shortfall": row["shortfall"],
        "surplus": row["surplus"],
        "coverage_status": row["coverage_status"],
    }


def _coverage_facts(coverage: Dict[str, Any]) -> Dict[str, Any]:
    """The authoritative figures, projected for the model.

    Shifts are ordered worst-first so that a roster larger than the cap loses
    its fully staffed tail rather than its shortages. ``shifts_total`` still
    reports the true count, so a truncated list cannot be mistaken for the
    whole picture — by the model or by anyone reading the context back.
    """
    rows = sorted(coverage["shifts"],
                  key=lambda row: (-row["shortfall"], -row["surplus"],
                                   row["shift_date"], row["start_time"]))
    return {
        "task": "summarise_staffing_coverage",
        "scope": coverage["filters"],
        "totals": coverage["summary"],
        "shifts": [_coverage_fact_shift(row) for row in rows[:_MAX_FACT_SHIFTS]],
        "shifts_described": min(len(rows), _MAX_FACT_SHIFTS),
        "shifts_total": len(rows),
    }


def _numbers_in(node: Any) -> set:
    """Every integer appearing anywhere in the authoritative facts.

    Digits inside strings count too, so a date or a time in the facts also
    licenses the model to mention it.
    """
    found = set()
    if isinstance(node, bool):
        return found
    if isinstance(node, int):
        found.add(node)
    elif isinstance(node, str):
        found.update(int(token) for token in re.findall(r"\d+", node))
    elif isinstance(node, dict):
        for value in node.values():
            found |= _numbers_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= _numbers_in(value)
    return found


def _numbers_claimed(text: str) -> set:
    """Every integer the narrative asserts, written as digits or as words."""
    found = {int(token) for token in re.findall(r"\d+", text)}
    lowered = text.lower()
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            found.add(value)
    return found


def _reject_invented_numbers(texts: List[str], supported: set) -> None:
    """Refuse a narrative that states a figure the roster does not support.

    The backend owns every staffing number in this system, so the narrative
    may borrow one but never produce one. A fluent summary asserting "three
    nurses short" when nobody is short is worse than no summary at all: it is
    indistinguishable from the real thing at a glance, and a manager would act
    on it.

    Deliberately fails CLOSED. An ordinary word like "one" is checked as a
    number, so a narrative can occasionally be rejected over a harmless turn
    of phrase. That costs a paragraph the manager never needed; the other
    direction costs them a wrong number they would trust.
    """
    for text in texts:
        unsupported = _numbers_claimed(text) - supported
        if unsupported:
            raise OllamaError(
                FALLBACK_UNSUPPORTED_NUMBERS,
                f"unsupported figures: {sorted(unsupported)}")


def _narrate_coverage(facts: Dict[str, Any]):
    """Ask the model to describe the position. Raises ``OllamaError`` on any miss."""
    reply = ollama_client.generate_json(
        prompt=coverage_summary_prompt.build_prompt(facts),
        system=coverage_summary_prompt.SYSTEM_PROMPT,
    )

    narrative = _clean_text(
        reply.get(coverage_summary_prompt.SUMMARY_KEY), _MAX_SUMMARY)
    if not narrative:
        raise OllamaError(REASON_INVALID_OUTPUT, "no usable summary text")

    raw = reply.get(coverage_summary_prompt.PRIORITIES_KEY) or []
    if not isinstance(raw, list):
        raise OllamaError(REASON_INVALID_OUTPUT, "priorities was not a list")
    priorities = []
    for item in raw[:_MAX_PRIORITIES]:
        cleaned = _clean_text(item, _MAX_PRIORITY)
        if cleaned:
            priorities.append(cleaned)

    # Checked only after cleaning, so the text validated is the text rendered.
    _reject_invented_numbers([narrative, *priorities], _numbers_in(facts))
    return narrative, priorities


def coverage_summary(department: Optional[str] = None,
                     shift_date: Optional[str] = None,
                     narrate: bool = False) -> Dict[str, Any]:
    """Return the coverage position, optionally narrated by the model.

    The deterministic figures are calculated first and unconditionally, and
    they are returned in full whatever happens next: ``headline``, ``summary``
    and ``gaps`` are the authoritative answer, and the narrative is commentary
    laid beside them. A manager reading a number is always reading the
    roster's number, never the model's.

    ``narrate`` is OPT-IN. The deterministic summary loads on every Workforce
    Overview page view, and a model call on every page view would be both slow
    and unasked for, so narration happens only when someone requests it.
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

    facts = _coverage_facts(coverage)
    narrative: Optional[str] = None
    priorities: List[str] = []
    mode = "rule-based"
    fallback_reason: Optional[str] = None

    if not narrate:
        fallback_reason = FALLBACK_NOT_REQUESTED
    elif not Config.AI_ENABLED:
        fallback_reason = FALLBACK_AI_DISABLED
    elif summary["total_shifts"] == 0:
        # Nothing to describe. Calling the model to say so would spend a
        # timeout budget to learn what the headline already states.
        fallback_reason = FALLBACK_NO_SHIFTS
    else:
        try:
            narrative, priorities = _narrate_coverage(facts)
            mode = "ai"
        except OllamaError as error:
            # The reason CODE is kept; the exception detail is not. A manager
            # needs to know the summary is deterministic, not which socket
            # failed or what the model actually said.
            fallback_reason = error.reason

    note = (_COVERAGE_FALLBACK_NOTES.get(fallback_reason,
                                         _COVERAGE_FALLBACK_NOTES[REASON_UNAVAILABLE])
            if fallback_reason is not None
            else ("Narrated by the local model from the roster's own figures. "
                  "A summary is not a decision; the manager acts."))

    return {
        "ai_enabled": Config.AI_ENABLED,
        "mode": mode,
        "note": note,
        "generation": {
            "source": "ollama" if mode == "ai" else "deterministic",
            "model": Config.OLLAMA_MODEL,
            "fallback_reason": fallback_reason,
        },
        # Authoritative, and present in every response regardless of the model.
        "headline": headline,
        "summary": summary,
        "gaps": gaps,
        "narrative": narrative,
        "priorities": priorities,
        "context": {**facts, "model": Config.OLLAMA_MODEL},
    }
