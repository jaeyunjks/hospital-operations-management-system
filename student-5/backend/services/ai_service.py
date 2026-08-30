"""AI preparation layer for the Student 5 backend/API microservice.

``suggest_staff`` ranks with Ollama when AI-Mode is on; ``coverage_summary``
optionally asks the same local model to interpret authoritative roster facts.

The pipeline is strictly ordered, and the order is the safety property:

    eligibility_service  ->  grounded candidate context  ->  optional Ollama
    ordering of eligible candidates only  ->  Staff Manager assigns manually

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
  the AI ordering, never the shortlist or its fact-composed explanations

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

#: Eligible staff exist, but none match the primary recommendation criteria.
FALLBACK_NO_PRIMARY_CANDIDATES = "no_primary_candidates"

#: Generated prose stated a number that was absent from the supplied facts.
FALLBACK_UNSUPPORTED_NUMBERS = "unsupported_numbers"

#: Generated prose asserted an employment-hours rule that is not configured.
FALLBACK_UNSUPPORTED_POLICY = "unsupported_policy"

#: Plain-English explanation per fallback code. These reach the API and the
#: manager, so they say what happened to the ranking — never which exception
#: was raised, which host refused, or what the model actually returned.
_FALLBACK_NOTES = {
    FALLBACK_AI_DISABLED: (
        "Deterministic ordering. AI-Mode is switched off."),
    FALLBACK_NO_CANDIDATES: (
        "No staff are eligible for this shift, so no ranking was requested."),
    FALLBACK_NO_PRIMARY_CANDIDATES: (
        "No eligible candidate matches the primary recommendation criteria. "
        "Other eligible staff are shown separately as alternatives."),
    REASON_UNAVAILABLE: (
        "Deterministic ordering. The ranking model could not be reached."),
    REASON_INVALID_OUTPUT: (
        "Deterministic ordering. The ranking model returned an unusable "
        "response."),
    FALLBACK_UNSUPPORTED_NUMBERS: (
        "Deterministic ordering. The generated reasoning contained figures "
        "the roster does not support, so it was discarded."),
    FALLBACK_UNSUPPORTED_POLICY: (
        "Deterministic ordering. The generated reasoning relied on an "
        "employment-hours policy that is not configured, so it was discarded."),
}

#: Narration was never asked for. Not a failure — the deterministic summary
#: loads on every page view, and only an explicit request calls the model.
FALLBACK_NOT_REQUESTED = "not_requested"

#: No shift matched the filters, so there was nothing to narrate.
FALLBACK_NO_SHIFTS = "no_shifts"

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
    FALLBACK_UNSUPPORTED_POLICY: (
        "Deterministic summary. The generated narrative relied on an "
        "employment-hours policy that is not configured, so it was discarded."),
}

#: A candidate explanation is a caption, not a report, so fact-composed text
#: is capped before it can reach a template.
_MAX_RATIONALE = 120

#: A coverage narrative is a short paragraph; its priorities are captions.
_MAX_SUMMARY = 600
_MAX_PRIORITY = 120
_MAX_PRIORITIES = 5
_MAX_CONSTRAINT = 240
_MAX_NEXT_ACTION = 240

#: At most this many shifts are described to the model. Ordered worst-first,
#: so a large roster is truncated from the fully staffed end where the detail
#: matters least; the totals still describe every shift.
_MAX_FACT_SHIFTS = 20
_MAX_GAP_ANALYSES = 10
_MAX_CONTEXT_CANDIDATES = 12
_MAX_PRIMARY_RECOMMENDATIONS = 3
_MAX_ALTERNATIVES = 3

#: Number words the validator understands, so "three nurses short" is checked
#: as carefully as "3 nurses short". Beyond twelve a model writes digits.
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}


def _llm_assignment(assignment: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Project a real assignment without ids, notes or approval metadata."""
    if not assignment:
        return None
    return {
        "department": assignment.get("department"),
        "shift_date": assignment.get("shift_date"),
        "start_time": assignment.get("start_time"),
        "end_time": assignment.get("end_time"),
        "required_role": assignment.get("required_role"),
        "assignment_status": assignment.get("assignment_status"),
    }


def _llm_candidate(candidate: Dict[str, Any], *, include_staff_id: bool = True
                   ) -> Dict[str, Any]:
    """Project one evaluated candidate down to what a model may be told.

    Names are withheld: ranking needs role, department, specialisation and the
    advisory availability flag, never identity, and the backend re-joins the
    staff_id to a name itself. Free-text ``notes`` on the staff record and the
    ``reason`` on an absence request are excluded outright — they can carry
    medical or personal detail that has no business in a prompt.

    Eligibility and blocking facts are included so the model can explain a
    genuine constraint. The projection never includes absence reasons, notes
    or names. Coverage narration also omits ``staff_id`` so it cannot invent
    or expose identities; ranking retains the id because it must return an
    order that the backend can validate.
    """
    projected = {
        "role": candidate["role"],
        "department": candidate["department"],
        "department_matches_shift": candidate.get("department_match"),
        "specialisation": candidate["specialisation"],
        "employment_status": candidate["employment_status"],
        "availability_status": candidate.get("availability_status"),
        "eligible": candidate.get("eligible"),
        "blocking_reason": candidate.get("blocked_reason"),
        "weekly_availability_matches": candidate["weekly_ok"],
        "weekly_rostered_hours": candidate.get("weekly_rostered_hours"),
        "current_assignments": [
            _llm_assignment(row)
            for row in candidate.get("current_assignments", [])
        ],
        "conflicting_assignment": _llm_assignment(
            candidate.get("conflicting_assignment")),
    }
    if include_staff_id:
        projected = {"staff_id": candidate["staff_id"], **projected}
    return projected


def _policy_facts() -> Dict[str, Any]:
    """Policy values actually configured in this release.

    There is deliberately no inferred default. Employment status and rostered
    hours are useful context, but without a real threshold they cannot prove
    an hours-policy risk.
    """
    return {
        "weekly_hours_limit": {
            "configured": False,
            "value": None,
            "note": "No weekly-hours policy is configured.",
        }
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


def _supported_candidate_rationale(candidate: Dict[str, Any]) -> str:
    """A concise trade-off statement composed only from verified fields."""
    department = ("Same department" if candidate.get("department_match")
                  else "Cross-department")
    weekly = ("weekly availability matches" if candidate.get("weekly_ok")
              else "outside weekly availability")
    hours = candidate.get("weekly_rostered_hours")
    workload = ("weekly rostered hours unavailable" if hours is None
                else f"{hours:g} rostered hours this week")
    employment = candidate.get("employment_status") or "Employment status unavailable"
    return f"{employment}; {department}; {weekly}; {workload}."[:_MAX_RATIONALE]


def _recommendation_priority(candidate: Dict[str, Any]):
    """Verified presentation preferences; never an eligibility decision."""
    hours = candidate.get("weekly_rostered_hours")
    return (
        not candidate.get("department_match", False),
        candidate.get("availability_status") != "Available",
        not candidate.get("weekly_ok", False),
        candidate.get("conflicting_assignment") is not None,
        hours is None,
        hours if hours is not None else float("inf"),
    )


def _ordered_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stable deterministic order for shortlist selection and alternatives."""
    return sorted(candidates, key=lambda candidate: (
        *_recommendation_priority(candidate), candidate.get("name") or ""))


def _supported_staffing_assessment(coverage: Dict[str, Any],
                                   eligible: List[Dict[str, Any]],
                                   ineligible: List[Dict[str, Any]]) -> str:
    """Grounded situation and manager action for the suggestion panel."""
    if coverage["shortfall"] == 0:
        return "This shift is fully covered; no additional assignment is required."
    if eligible:
        return (
            "Eligible staff are identified, so this gap is not caused by a lack "
            "of candidates. Review the recommended shortlist and assign one if "
            "operationally appropriate.")
    return (
        "No eligible staff are currently identified. "
        + _blocked_evidence(ineligible)
        + " Manager review is required before qualified cover can be arranged.")


def _ranked_staff_id(entry: Any) -> Optional[int]:
    """The staff_id in one ranking entry, or None if there isn't a usable one.

    Accepts the documented ``{"staff_id": 3, ...}`` and a bare ``3``, because a
    smaller model will occasionally return the shorter form and rejecting the
    whole ranking over it would cost the manager the AI ordering for nothing.
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
        # The model decides order only. Displayed reasoning is assembled from
        # the verified candidate fields, so free-form prose cannot invent a
        # conflict, policy, availability state or workload claim.
        candidate["rationale"] = _supported_candidate_rationale(candidate)
        ordered.append(candidate)

    if not ordered:
        raise OllamaError(REASON_INVALID_OUTPUT, "no known staff_id in ranking")

    # Whoever the model left out keeps their deterministic position, after
    # everyone it did rank.
    for candidate in shortlist:
        if candidate["staff_id"] in seen:
            continue
        grounded = dict(candidate)
        grounded["rationale"] = _supported_candidate_rationale(grounded)
        ordered.append(grounded)
    return ordered


def _rank_with_model(shift: Dict[str, Any],
                     shortlist: List[Dict[str, Any]],
                     alternatives: List[Dict[str, Any]],
                     ineligible: List[Dict[str, Any]],
                     coverage: Dict[str, Any]):
    """Ask the model to order candidates, without delegating eligibility."""
    policies = _policy_facts()
    supported_assessment = _supported_staffing_assessment(
        coverage, shortlist, ineligible)
    prompt_facts = {
        "shift": _llm_shift(shift),
        "coverage_gap": coverage,
        "eligible_candidates": [_llm_candidate(row) for row in shortlist],
        "eligible_alternatives": [
            _llm_candidate(row) for row in alternatives[:_MAX_ALTERNATIVES]
        ],
        "ineligible_candidates": [
            _llm_candidate(row) for row in ineligible[:_MAX_CONTEXT_CANDIDATES]
        ],
        "ineligible_candidates_total": len(ineligible),
        "configured_policies": policies,
        "supported_assessment": supported_assessment,
    }
    reply = ollama_client.generate_json(
        prompt=suggest_staff_prompt.build_prompt(prompt_facts),
        system=suggest_staff_prompt.SYSTEM_PROMPT,
    )
    ordered = _merge_ranking(
        shortlist, reply.get(suggest_staff_prompt.RANKING_KEY))
    # The model may break ties, but it cannot reverse verified operational
    # preferences such as matching availability or lower rostered hours.
    ordered.sort(key=_recommendation_priority)
    assessment = supported_assessment
    model_text = [assessment]
    model_text.extend(
        row["rationale"] for row in ordered if row.get("rationale"))
    _reject_invented_numbers(model_text, _numbers_in(prompt_facts))
    _reject_unsupported_policy_claims(model_text, prompt_facts)
    return ordered, assessment, prompt_facts


def _blocked_evidence(candidates: List[Dict[str, Any]]) -> str:
    """Compact deterministic explanation for an empty eligible shortlist."""
    reasons: Dict[str, int] = {}
    for candidate in candidates:
        reason = candidate.get("blocked_reason")
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    if not reasons:
        return "The available data does not indicate why this shift remains unfilled."
    details = "; ".join(
        f"{reason} ({count})" for reason, count in
        sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:3])
    return "Recorded blocking reasons: " + details + "."


def suggest_staff(shift_id: int, limit: int = 5) -> Dict[str, Any]:
    """Return the ELIGIBLE staff for a shift, ranked, with the LLM context.

    Deterministic eligibility runs first and unconditionally. Ranking is an
    optional layer on top: when AI-Mode is on and there is something to rank,
    the shortlist goes to Ollama and comes back reordered. Candidate trade-offs
    and the staffing assessment are composed from verified fields rather than
    free-form model text. Every failure — off, unreachable, slow, or unusable
    output — lands on the same deterministic ordering the caller would have
    got anyway, with ``mode`` and ``ranking.fallback_reason`` saying so plainly.

    The model ranks the shortlist AFTER ``limit`` has been applied, not the
    full eligible set. Capping first keeps the model to reordering the same
    people a manager would already have seen; letting it rank first would let
    it decide who appears at all, which is a larger delegation than this
    feature intends.

    ``limit`` caps the shortlist only. ``eligible_count`` reports how many
    passed the rules, so a manager can tell a short list from a scarce one.
    Nothing here assigns anyone: the manager still picks from the list.
    """
    result = eligibility_service.candidates_for_shift(shift_id)
    eligible = [row for row in result["candidates"] if row["eligible"]]
    ineligible = [row for row in result["candidates"] if not row["eligible"]]
    ordered_eligible = _ordered_candidates(eligible)
    primary_pool = [
        candidate for candidate in ordered_eligible
        if candidate.get("availability_status") == "Available"
        and candidate.get("weekly_ok")
        and candidate.get("conflicting_assignment") is None
    ]
    primary_limit = min(limit, _MAX_PRIMARY_RECOMMENDATIONS)
    shortlist = [dict(candidate) for candidate in primary_pool[:primary_limit]]
    primary_ids = {candidate["staff_id"] for candidate in shortlist}
    alternative_rows = [candidate for candidate in ordered_eligible
                        if candidate["staff_id"] not in primary_ids]
    alternatives = []
    for candidate in alternative_rows[:_MAX_ALTERNATIVES]:
        grounded = dict(candidate)
        grounded["rationale"] = _supported_candidate_rationale(grounded)
        alternatives.append(grounded)

    assigned = len(result["already_assigned_staff_ids"])
    required = int(result["shift"]["required_staff_count"])
    coverage = {
        "required_positions": required,
        "assigned_positions": assigned,
        "shortfall": max(required - assigned, 0),
        "surplus": max(assigned - required, 0),
    }

    suggestions = []
    for candidate in shortlist:
        grounded = dict(candidate)
        grounded["rationale"] = _supported_candidate_rationale(grounded)
        suggestions.append(grounded)
    assessment: Optional[str] = _supported_staffing_assessment(
        coverage, eligible, ineligible)
    mode = "rule-based"
    fallback_reason: Optional[str] = None
    prompt_facts = {
        "shift": _llm_shift(result["shift"]),
        "coverage_gap": coverage,
        "eligible_candidates": [_llm_candidate(row) for row in shortlist],
        "eligible_alternatives": [
            _llm_candidate(row) for row in alternatives
        ],
        "ineligible_candidates": [
            _llm_candidate(row) for row in ineligible[:_MAX_CONTEXT_CANDIDATES]
        ],
        "ineligible_candidates_total": len(ineligible),
        "configured_policies": _policy_facts(),
        "supported_assessment": assessment,
    }

    if not Config.AI_ENABLED:
        fallback_reason = FALLBACK_AI_DISABLED
    elif not shortlist:
        # Nothing suitable for the primary shortlist. Calling the model to
        # sort an empty list would spend a timeout budget to learn what is
        # already known; eligible but advisory-mismatched staff remain visible
        # as alternatives.
        fallback_reason = (FALLBACK_NO_PRIMARY_CANDIDATES if eligible
                           else FALLBACK_NO_CANDIDATES)
    else:
        try:
            suggestions, assessment, prompt_facts = _rank_with_model(
                result["shift"], shortlist, alternatives, ineligible, coverage)
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
    if fallback_reason == FALLBACK_NO_CANDIDATES:
        note += " " + _blocked_evidence(ineligible)

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
        "alternatives": alternatives,
        "assessment": assessment,
        "context": {
            "task": "suggest_staff_for_shift",
            **prompt_facts,
            # Backward-compatible aliases retained for current clients.
            "candidate_count": len(eligible),
            "candidates": prompt_facts["eligible_candidates"],
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
        "configured_policies": _policy_facts(),
        "gap_analysis": [],
    }


def _coverage_gap_analysis(gaps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ground each visible gap in the same eligibility facts as the drawer.

    Names and internal ids are deliberately absent. The summary needs to know
    whether realistic cover exists and what blocks it, not who a person is.
    Candidate lists are capped but their true totals remain explicit.
    """
    analysed = []
    for gap in gaps[:_MAX_GAP_ANALYSES]:
        result = eligibility_service.candidates_for_shift(gap["shift_id"])
        eligible = [row for row in result["candidates"] if row["eligible"]]
        ineligible = [row for row in result["candidates"] if not row["eligible"]]
        reason_counts: Dict[str, int] = {}
        for candidate in ineligible:
            reason = candidate.get("blocked_reason")
            if reason:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if eligible:
            constraint = (
                "Eligible staff are identified; the available data does not "
                "indicate why this shift remains unfilled.")
            manager_action = (
                "Review the eligible candidates and assign one if operationally "
                "appropriate.")
            constraint_status = "eligible_candidates_available"
        elif reason_counts:
            reasons = "; ".join(sorted(reason_counts))
            constraint = (
                "No eligible staff are currently identified. Recorded blocking "
                f"reasons: {reasons}.")
            manager_action = (
                "Review the recorded blocking reasons and arrange qualified "
                "cover if they cannot be resolved.")
            constraint_status = "recorded_blockers"
        else:
            constraint = (
                "No eligible staff are currently identified. The available data "
                "does not indicate why this shift remains unfilled.")
            manager_action = (
                "Review the shift and workforce records; no eligible candidate "
                "is currently identified.")
            constraint_status = "insufficient_candidate_evidence"

        analysed.append({
            "shift": _coverage_fact_shift(gap),
            "constraint_status": constraint_status,
            "recorded_blocking_reasons": reason_counts,
            "supported_constraint": constraint,
            "supported_manager_action": manager_action,
            "eligible_candidates": [
                _llm_candidate(row, include_staff_id=False)
                for row in eligible[:_MAX_CONTEXT_CANDIDATES]
            ],
            "eligible_candidates_total": len(eligible),
            "ineligible_candidates": [
                _llm_candidate(row, include_staff_id=False)
                for row in ineligible[:_MAX_CONTEXT_CANDIDATES]
            ],
            "ineligible_candidates_total": len(ineligible),
        })
    analysed.sort(key=lambda row: (
        row["shift"]["assigned_positions"] != 0,
        row["eligible_candidates_total"] != 0,
        -row["shift"]["shortfall"],
        row["shift"]["shift_date"],
        row["shift"]["start_time"],
    ))
    for priority, row in enumerate(analysed, start=1):
        row["priority_order"] = priority
    return analysed


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
    elif isinstance(node, float):
        found.update(int(token) for token in re.findall(r"\d+", str(node)))
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


def _reject_unsupported_policy_claims(texts: List[str],
                                      facts: Dict[str, Any]) -> None:
    """Reject an asserted hours-policy rule when no such rule exists.

    Explicit uncertainty remains valid (for example, "No weekly-hours policy
    is configured"). What is rejected is a positive claim such as exceeding a
    limit, overtime, or breaching a cap when the supplied policy value is null.
    """
    configured = (facts.get("configured_policies", {})
                  .get("weekly_hours_limit", {}).get("configured"))
    if configured:
        return
    assertion = re.compile(
        r"\b(?:exceed(?:s|ed|ing)?|breach(?:es|ed|ing)?|overtime|"
        r"maximum|hour(?:s)?\s+limit|weekly\s+limit|hours?\s+cap)\b",
        re.IGNORECASE)
    uncertainty = re.compile(
        r"\b(?:no|not|without|unknown|unavailable|cannot|can't|insufficient)\b"
        r".{0,80}\b(?:policy|limit|threshold|cap|risk)\b",
        re.IGNORECASE)
    for text in texts:
        if assertion.search(text) and not uncertainty.search(text):
            raise OllamaError(
                FALLBACK_UNSUPPORTED_POLICY,
                "weekly-hours policy claim without a configured policy")


def _reject_unstaffed_claim_when_covered(texts: List[str],
                                         facts: Dict[str, Any]) -> None:
    """A fully covered roster cannot acquire a problem through prose."""
    if facts.get("totals", {}).get("total_shortfall") != 0:
        return
    issue = re.compile(
        r"\b(?:understaffed|unstaffed|shortage|shortfall|unfilled|staffing gap|"
        r"short by|needs cover)\b", re.IGNORECASE)
    if any(issue.search(text) for text in texts):
        raise OllamaError(REASON_INVALID_OUTPUT,
                          "summary asserted a gap on a fully covered roster")


def _validate_grounded_action(constraint: Optional[str],
                              next_action: Optional[str],
                              facts: Dict[str, Any]) -> None:
    """Require cause/action text to be copied from backend-derived evidence."""
    if facts.get("totals", {}).get("total_shortfall", 0) <= 0:
        return
    primary = facts.get("primary_issue") or {}
    if (constraint != primary.get("constraint")
            or next_action != primary.get("manager_action")):
        raise OllamaError(
            REASON_INVALID_OUTPUT,
            "constraint or action did not match the grounded primary issue")


def _validate_freeform_blocker_claims(texts: List[str],
                                      facts: Dict[str, Any]) -> None:
    """Reject blocker vocabulary absent from the grounded primary issue."""
    patterns = {
        "unavailable": re.compile(r"\bunavailable\b", re.IGNORECASE),
        "on leave": re.compile(r"\bon leave\b", re.IGNORECASE),
        "role mismatch": re.compile(r"\brole mismatch\b", re.IGNORECASE),
        "already assigned": re.compile(r"\balready assigned\b", re.IGNORECASE),
        "conflict": re.compile(
            r"\b(?:conflict(?:ing|ed)?|overlap(?:ping)?)\b", re.IGNORECASE),
    }
    primary = ((facts.get("gap_analysis") or [{}])[0])
    grounded = " ".join(primary.get("recorded_blocking_reasons", {})).lower()
    allowed = {name for name, pattern in patterns.items()
               if pattern.search(grounded)}
    for text in texts:
        claimed = {name for name, pattern in patterns.items()
                   if pattern.search(text)}
        if not claimed <= allowed:
            raise OllamaError(
                REASON_INVALID_OUTPUT,
                "summary asserted a blocker absent from the primary issue")


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

    constraint = _clean_text(
        reply.get(coverage_summary_prompt.CONSTRAINT_KEY), _MAX_CONSTRAINT)
    next_action = _clean_text(
        reply.get(coverage_summary_prompt.NEXT_ACTION_KEY), _MAX_NEXT_ACTION)
    has_gap = facts.get("totals", {}).get("total_shortfall", 0) > 0
    if has_gap and (not constraint or not next_action):
        raise OllamaError(
            REASON_INVALID_OUTPUT,
            "gap narration omitted its constraint or manager action")
    if not has_gap:
        # A smaller model may echo the gap-shaped example despite the explicit
        # covered-roster instruction. These fields are structurally irrelevant
        # when there is no gap, so discard them before any claim validation.
        constraint = None
        next_action = None

    raw = reply.get(coverage_summary_prompt.PRIORITIES_KEY) or []
    if not isinstance(raw, list):
        raise OllamaError(REASON_INVALID_OUTPUT, "priorities was not a list")
    priorities = []
    for item in raw[:_MAX_PRIORITIES]:
        cleaned = _clean_text(item, _MAX_PRIORITY)
        if cleaned:
            priorities.append(cleaned)

    # Checked only after cleaning, so the text validated is the text rendered.
    _validate_grounded_action(constraint, next_action, facts)
    _validate_freeform_blocker_claims([narrative, *priorities], facts)

    rendered = [narrative, *priorities]
    if constraint:
        rendered.append(constraint)
    if next_action:
        rendered.append(next_action)
    _reject_invented_numbers(rendered, _numbers_in(facts))
    _reject_unsupported_policy_claims(rendered, facts)
    _reject_unstaffed_claim_when_covered(rendered, facts)
    # The model response is still requested and validated, but the visible
    # summary is deliberately composed from the same authoritative evidence as
    # the constraint/action fields. This keeps the panel operationally useful
    # instead of echoing the dashboard's counts and percentages.
    if not has_gap:
        narrative = "No staffing intervention is required for the selected scope."
    else:
        primary = (facts.get("gap_analysis") or [{}])[0]
        narrative = (
            "Eligible staff are identified, so this gap is not caused by a "
            "lack of candidates."
            if primary.get("eligible_candidates_total", 0) > 0
            else None
        )
    priorities = []
    return narrative, constraint, next_action, priorities


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
    constraint: Optional[str] = None
    next_action: Optional[str] = None
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
            # Cause analysis is only fetched for an explicit AI request. The
            # normal overview remains the same fast deterministic coverage
            # query and never performs candidate evaluation on page load.
            facts["gap_analysis"] = _coverage_gap_analysis(gaps)
            facts["gaps_analysed"] = min(len(gaps), _MAX_GAP_ANALYSES)
            facts["gaps_total"] = len(gaps)
            primary = facts["gap_analysis"][0] if facts["gap_analysis"] else None
            facts["primary_issue"] = ({
                "shift": primary["shift"],
                "constraint": primary["supported_constraint"],
                "manager_action": primary["supported_manager_action"],
            } if primary else None)
            narrative, constraint, next_action, priorities = _narrate_coverage(facts)
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
        "constraint": constraint,
        "next_action": next_action,
        "priorities": priorities,
        "context": {**facts, "model": Config.OLLAMA_MODEL},
    }
