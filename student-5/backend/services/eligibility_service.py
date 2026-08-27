"""Deterministic candidate eligibility for Student 5 — Staff & Shift Management.

SINGLE SOURCE OF TRUTH for who may be assigned to a shift. Both consumers call
this module: the manual candidate list rendered in the shift drawer, and the
``/api/shifts/suggest-staff`` endpoint the AI ranking will later sit on top of.
Neither re-implements a rule of its own, so the two can never disagree about
who is assignable.

Eligibility is a yes/no decision derived from real availability and assignment
data. It is deliberately NOT a score. A weighted total can let a strong match
on one axis outvote a genuine blocker — a role mismatch or an approved absence
is not something a high department score should be able to overcome — and it
gives the manager a number instead of a reason.

HARD RULES — these block assignment
    * already assigned to this shift
    * operational availability_status of 'Unavailable' or 'On Leave'
    * an Approved temporary-unavailability request covering the shift date
    * the staff member does not hold the shift's required_role
    * an overlapping active assignment on another shift

ADVISORY — reported, never blocking
    * recurring weekly availability. The pattern is a normal expectation, not
      a contract: a manager may legitimately roster outside it, and the assign
      endpoint permits it. Blocking here would contradict the API.

CONTEXT ONLY — never blocks, never scores
    * department and specialisation. Department orders the list so local staff
      surface first, but it must never make anyone unassignable: covering
      another ward's gap is precisely what a manager needs to be able to do.

When AI ranking is added it re-orders and explains the ELIGIBLE list produced
here. It is never given the ineligible, and it never decides eligibility.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from database_client import database_client
from services import assignment_service, request_service
from validation import MINUTES_PER_DAY, MINUTES_PER_WEEK, weekly_period_segments

#: Assignment states that no longer occupy a place on a shift, so they neither
#: block a candidate nor count as a clash.
INACTIVE_ASSIGNMENT = assignment_service.INACTIVE_STATUSES

#: Operational statuses that make someone unassignable, with the wording the
#: blocked reason uses verbatim.
BLOCKING_AVAILABILITY = ("On Leave", "Unavailable")

#: Employment groupings used ONLY to order the candidate list, so permanent
#: staff surface above casual cover. A presentation aid, not a policy rule and
#: not a score — it can never change whether someone is eligible.
_EMPLOYMENT_PRIORITY = {"Full-Time": 0, "Part-Time": 0, "Casual": 1, "Contract": 1}


# ------------------------------------------------------------------- times
def _time_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _shift_minutes_on(shift: Dict[str, Any]):
    """Absolute (start, end) minutes for a shift, anchored to its own date.

    An overnight shift (end <= start) runs into the following day, so its end
    is pushed a day forward rather than being treated as invalid.
    """
    try:
        day = datetime.datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return None
    base = day.toordinal() * MINUTES_PER_DAY
    start = base + _time_minutes(shift["start_time"])
    end = base + _time_minutes(shift["end_time"])
    if end <= start:
        end += MINUTES_PER_DAY
    return (start, end)


def shifts_overlap(first: Dict[str, Any], second: Dict[str, Any]) -> bool:
    """True when two dated shifts share any minute of clock time."""
    a, b = _shift_minutes_on(first), _shift_minutes_on(second)
    if not a or not b:
        return False
    return a[0] < b[1] and b[0] < a[1]


def weekly_covers_shift(periods: Sequence[Dict[str, Any]],
                        shift: Dict[str, Any]) -> bool:
    """True when the recurring pattern covers the shift's WHOLE window.

    Compared on the seven-day timeline used by the weekly availability grid,
    so overnight periods and the Sunday-to-Monday wrap behave consistently.
    Touching the shift is not enough: every minute must be covered, otherwise
    a one-hour period would vouch for an eight-hour shift.
    """
    span = _shift_minutes_on(shift)
    if not span or not periods:
        return False
    try:
        day = datetime.datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return False

    length = span[1] - span[0]
    start_in_week = day.weekday() * MINUTES_PER_DAY + _time_minutes(shift["start_time"])
    end_in_week = start_in_week + length

    shift_segments = []
    if end_in_week <= MINUTES_PER_WEEK:
        shift_segments.append((start_in_week, end_in_week))
    else:
        shift_segments.append((start_in_week, MINUTES_PER_WEEK))
        shift_segments.append((0, end_in_week - MINUTES_PER_WEEK))

    available: List[Any] = []
    for period in periods:
        available.extend(weekly_period_segments(
            period["day_of_week"], period["start_time"], period["end_time"]))

    # Walk each segment forward through the available intervals. Anything left
    # uncovered when no interval can advance the cursor is a genuine gap.
    for seg_start, seg_end in shift_segments:
        cursor = seg_start
        progressed = True
        while cursor < seg_end and progressed:
            progressed = False
            for a_start, a_end in available:
                if a_start <= cursor < a_end:
                    cursor = min(a_end, seg_end)
                    progressed = True
                    break
        if cursor < seg_end:
            return False
    return True


# ------------------------------------------------------------------ labels
def _date_compact(value: Optional[str]) -> Optional[str]:
    """e.g. "1 Sep" — for period labels where the year is already obvious."""
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%d").strftime("%-d %b")
    except (TypeError, ValueError):
        return value


def request_period_label(record: Dict[str, Any]) -> str:
    """"1 Sep – 3 Sep", collapsing a single-day request to just "1 Sep"."""
    start = _date_compact(record.get("start_date"))
    end = _date_compact(record.get("end_date"))
    return start if start == end else f"{start} – {end}"


# ------------------------------------------------------------- the decision
def evaluate_candidate(person: Dict[str, Any], shift: Dict[str, Any],
                       assigned_ids: Set[Any],
                       their_shifts: Iterable[Dict[str, Any]],
                       weekly_periods: Sequence[Dict[str, Any]],
                       approved_requests: Iterable[Dict[str, Any]] = ()
                       ) -> Dict[str, Any]:
    """Decide whether one staff member may be assigned to one shift.

    Pure: every input is passed in, nothing is fetched and nothing is written.
    The rules are ordered so the reported reason is the most fundamental one —
    being unavailable is a truer explanation than a downstream rota clash.
    """
    notes: List[Dict[str, Any]] = []
    blocked: Optional[str] = None
    approved = request_service.blocking_request_in(
        approved_requests, shift.get("shift_date"))

    if person["staff_id"] in assigned_ids:
        blocked = "Already assigned to this shift"
    elif person.get("availability_status") == "On Leave":
        blocked = "On Leave"
    elif person.get("availability_status") == "Unavailable":
        blocked = "Unavailable"
    elif approved is not None:
        blocked = "Temporarily unavailable " + request_period_label(approved)
    elif person.get("role") != shift.get("required_role"):
        blocked = "Role mismatch"
    else:
        clash = next((row for row in their_shifts
                      if row.get("shift_id") != shift.get("shift_id")
                      and row.get("assignment_status") not in INACTIVE_ASSIGNMENT
                      and shifts_overlap(row, shift)), None)
        if clash:
            blocked = ("Already rostered " + clash.get("shift_date", "") + " "
                       + clash.get("start_time", "") + "-" + clash.get("end_time", ""))

    # Advisory only. Recorded whether or not the candidate is blocked, so the
    # manager sees the full picture rather than one reason at a time.
    covered = weekly_covers_shift(weekly_periods, shift)
    notes.append({"ok": covered,
                  "text": "Weekly availability matches" if covered
                          else "Outside weekly availability"})

    return {
        "staff_id": person["staff_id"],
        "name": person.get("name"),
        "role": person.get("role"),
        "specialisation": person.get("specialisation"),
        "department": person.get("department"),
        "employment_status": person.get("employment_status"),
        "availability_status": person.get("availability_status"),
        "eligible": blocked is None,
        "blocked_reason": blocked,
        "weekly_ok": covered,
        "notes": notes,
        "approved_request": approved,
    }


def _sort_key(candidate: Dict[str, Any], shift: Dict[str, Any]):
    """Eligible first, then advisory match, then ordering aids. Ordering only.

    Nothing below the first key can make an ineligible candidate outrank an
    eligible one, which is what keeps this an ordering and not a score.
    """
    return (
        not candidate["eligible"],
        not candidate["weekly_ok"],
        _EMPLOYMENT_PRIORITY.get(candidate.get("employment_status"), 2),
        candidate.get("department") != shift.get("department"),
        candidate.get("name") or "",
    )


def candidates_for_shift(shift_id: int) -> Dict[str, Any]:
    """Evaluate every staff member holding the shift's required role.

    Returns ALL of them, ineligible included, each carrying its blocking
    reason. Filtering happens at the point of use: the manual list shows
    everyone so a manager can see why someone cannot be used, while
    suggest-staff keeps only the eligible.

    Approved requests are fetched once for the whole workforce and grouped
    rather than queried per candidate, and a failure to load them is allowed
    to propagate: a candidate list that silently omits the absence rule would
    present someone on approved leave as assignable.
    """
    shift = database_client.get_shift(shift_id)          # raises NotFoundError
    assigned_ids = {row["staff_id"]
                    for row in assignment_service.active_assignments(shift_id)}

    approved_by_staff: Dict[Any, List[Dict[str, Any]]] = {}
    for record in database_client.list_unavailability_requests(
            request_status=request_service.BLOCKING_STATUS):
        approved_by_staff.setdefault(record.get("staff_id"), []).append(record)

    evaluated = []
    for person in database_client.list_staff(role=shift["required_role"]):
        staff_id = person["staff_id"]
        evaluated.append(evaluate_candidate(
            person, shift, assigned_ids,
            database_client.list_shifts_for_staff(staff_id),
            database_client.list_weekly_availability(staff_id),
            approved_by_staff.get(staff_id, ()),
        ))

    evaluated.sort(key=lambda candidate: _sort_key(candidate, shift))

    return {
        "shift": shift,
        "candidates": evaluated,
        "already_assigned_staff_ids": sorted(assigned_ids),
        "eligible_count": sum(1 for row in evaluated if row["eligible"]),
    }


def eligible_candidates(shift_id: int) -> Dict[str, Any]:
    """``candidates_for_shift`` reduced to those who may actually be assigned."""
    result = candidates_for_shift(shift_id)
    result["candidates"] = [row for row in result["candidates"] if row["eligible"]]
    return result
