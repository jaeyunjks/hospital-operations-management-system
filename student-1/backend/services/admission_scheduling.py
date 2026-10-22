"""Build a read-only scheduling review from Student-1 admission records."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any


CLINIC_OPEN = time(8, 0)
CLINIC_CLOSE = time(18, 0)
SLOT_MINUTES = 60
MAX_OPTIONS = 3
MAX_DURATION_MINUTES = 480
HUMAN_APPROVAL = (
    "A receptionist or system administrator must review the proposal; "
    "no admission is created or changed by this workflow."
)
NO_CAPACITY_CLAIM = (
    "Student-1 has no authoritative future slot-capacity source. "
    "A conflict-free option is not a confirmed booking or capacity guarantee."
)


class SchedulingInputError(ValueError):
    """The requested scheduling window is outside the supported contract."""


def _parse_date(value: Any) -> date:
    if not isinstance(value, str):
        raise SchedulingInputError("Enter a valid future date.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise SchedulingInputError("Enter a valid future date.") from error
    if parsed < date.today():
        raise SchedulingInputError("The scheduling date must not be in the past.")
    return parsed


def follow_up_request(evidence: Any) -> tuple[dict[str, Any], list[str]]:
    """Recover the request and already-reviewed times from a reviewed workflow."""
    try:
        reviewed = list(evidence["previously_reviewed_times"]) + [
            option["label"] for option in evidence["options"]
        ]
        request = {
            "date": evidence["date"],
            "duration_minutes": evidence["duration_minutes"],
        }
    except (KeyError, TypeError):
        raise SchedulingInputError("The reviewed proposal could not be read.") from None
    return request, reviewed


def build_review_evidence(
    payload: Any, admission_rows: Any, already_reviewed: Any = ()
) -> tuple[str, dict[str, Any]]:
    """Validate a proposed window and build options without changing admissions."""
    if not isinstance(payload, dict) or set(payload) != {
        "date", "duration_minutes"
    }:
        raise SchedulingInputError("Provide a date and duration.")
    if not isinstance(admission_rows, list) or any(
        not isinstance(row, dict) for row in admission_rows
    ):
        raise SchedulingInputError("Admission schedule data could not be verified.")

    schedule_date = _parse_date(payload["date"])
    raw_duration = payload["duration_minutes"]
    if (
        isinstance(raw_duration, bool)
        or not isinstance(raw_duration, (str, int))
        or isinstance(raw_duration, str) and not raw_duration.isdigit()
    ):
        raise SchedulingInputError("Enter a valid admission duration.")
    try:
        duration = int(raw_duration)
    except (TypeError, ValueError, OverflowError) as error:
        raise SchedulingInputError("Enter a valid admission duration.") from error
    if duration < 15 or duration > MAX_DURATION_MINUTES or duration % 15:
        raise SchedulingInputError(
            "Duration must be 15-minute increments from 15 to 480 minutes."
        )

    day_start = datetime.combine(schedule_date, CLINIC_OPEN)
    day_end = datetime.combine(schedule_date, CLINIC_CLOSE) - timedelta(minutes=duration)
    now = datetime.now()
    starts = []
    cursor = day_start
    while cursor <= day_end:
        if cursor > now:
            starts.append(cursor)
        cursor += timedelta(minutes=SLOT_MINUTES)
    if not starts:
        raise SchedulingInputError(
            "No future start times fit within clinic hours (08:00–18:00) for that date and duration."
        )

    relevant_admissions = []
    schedule_evidence_complete = True
    unbounded_other_day_count = 0
    for row in admission_rows:
        if row.get("admission_status") not in {"Pending", "Active"}:
            continue
        raw_start = row.get("admission_date")
        raw_end = row.get("admission_end")
        try:
            existing_start = datetime.fromisoformat(str(raw_start))
        except (TypeError, ValueError):
            schedule_evidence_complete = False
            continue
        try:
            existing_end = datetime.fromisoformat(str(raw_end))
        except (TypeError, ValueError):
            existing_end = None
            if existing_start.date() == schedule_date:
                schedule_evidence_complete = False
            else:
                unbounded_other_day_count += 1
                continue
        if existing_start.tzinfo is not None or (
            existing_end is not None and existing_end.tzinfo is not None
        ):
            schedule_evidence_complete = False
            continue
        if existing_end is not None and existing_end <= existing_start:
            schedule_evidence_complete = False
            continue
        relevant_admissions.append((existing_start, existing_end))

    reviewed = set(already_reviewed)
    candidates = []
    for start in starts:
        end = start + timedelta(minutes=duration)
        overlaps = any(
            start < (existing_end or datetime.max)
            and end > existing_start
            for existing_start, existing_end in relevant_admissions
        )
        label = f"{start:%H:%M}-{end:%H:%M}"
        if label not in reviewed:
            candidates.append((overlaps, start, label))
    if not candidates:
        raise SchedulingInputError("No further candidate times remain to review.")
    chosen = sorted(sorted(candidates)[:MAX_OPTIONS], key=lambda item: item[1])

    options = []
    for index, (overlaps, _start, label) in enumerate(chosen, start=1):
        options.append({
            "id": f"slot-{index}",
            "label": label,
            "attributes": {
                "has_recorded_admission_overlap": overlaps,
                "schedule_evidence_complete": schedule_evidence_complete,
            },
            "outcomes": {"metric_delta": 1},
            "risk_indicators": (
                ["Overlaps a recorded Pending or Active admission."]
                if overlaps else []
            ),
        })

    evidence = {
        "evidence_type": "student1_admission_scheduling",
        "date": schedule_date.isoformat(),
        "clinic_hours": f"{CLINIC_OPEN:%H:%M}-{CLINIC_CLOSE:%H:%M}",
        "duration_minutes": duration,
        "candidate_times_found": len(starts),
        "candidate_times_included": len(options),
        "previously_reviewed_times": list(already_reviewed),
        "objective": {
            "metric": "admission_time_proposed",
            "current": 0,
            "target": 1,
            "unit": "time",
        },
        "pending_or_active_admission_count": len(relevant_admissions),
        "schedule_evidence_complete": schedule_evidence_complete,
        "options": options,
        "constraints": [
            {
                "id": "no_recorded_admission_overlap",
                "scope": "option",
                "type": "hard",
                "field": "has_recorded_admission_overlap",
                "operator": "equals",
                "value": False,
                "description": "Do not recommend a time overlapping a recorded Pending or Active admission.",
            },
            {
                "id": "complete_schedule_evidence",
                "scope": "option",
                "type": "hard",
                "field": "schedule_evidence_complete",
                "operator": "equals",
                "value": True,
                "description": "Only recommend an option when relevant admission times can be checked.",
            },
            {"id": "human_approval", "scope": "workflow", "type": "approval",
             "description": HUMAN_APPROVAL},
            {"id": "no_mutation", "scope": "workflow", "type": "safety",
             "description": "This workflow must not create, update, or delete any patient or admission record."},
        ],
        "scheduling_rules": [
            "Candidate starts are hourly within clinic hours (assumed 08:00-18:00).",
            "Durations use 15-minute increments.",
        ],
        "unknowns": [NO_CAPACITY_CLAIM] + (
            [
                f"{unbounded_other_day_count} recorded admission(s) have no end time "
                "and start on another date; they were not compared."
            ]
            if unbounded_other_day_count else []
        ),
        "evidence_mode": "student1_admission_records",
    }
    task = (
        "Review the listed admission time options against the hard constraints. "
        "Conflict-free means no overlap with recorded admissions, not confirmed capacity. "
        "Do not make any record changes."
    )
    return task, evidence
