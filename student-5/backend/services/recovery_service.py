"""Read-only evidence assembly for the Student 5 staffing-recovery workflow.

This module deliberately owns no staffing rules.  It projects the existing
shift, coverage, assignment, and deterministic eligibility service outputs
into the generic evidence shape accepted by the shared Multi-Agent Server.
It does not call that server and it never performs a database mutation.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from errors import DatabaseServiceError
from services import (assignment_service, coverage_service,
                      eligibility_service, shift_service)


SCHEMA_VERSION = "1.0"
EVIDENCE_TYPE = "student5_staffing_recovery"
EVIDENCE_MODE = "live_api"

UNKNOWNS = [
    "No weekly-hours, overtime, fatigue, or consecutive-shift threshold is configured.",
    "No authoritative department-to-ward mapping links this shift to ward occupancy.",
    "Patient count and acuity are not available for the selected shift.",
    "Required staff count is manager-entered and is not derived from a clinical staffing ratio.",
]

_SHIFT_FIELDS = (
    "shift_id",
    "department",
    "shift_date",
    "start_time",
    "end_time",
    "required_role",
    "required_staff_count",
    "shift_status",
)
_COVERAGE_FIELDS = (
    "required_staff_count",
    "assigned_staff_count",
    "filled_staff_count",
    "shortfall",
    "surplus",
    "coverage_status",
)
_CANDIDATE_REQUIRED_FIELDS = (
    "staff_id",
    "eligible",
    "weekly_ok",
    "department_match",
)


def _unavailable(message: str) -> DatabaseServiceError:
    """Use the backend's existing fail-closed downstream-data convention."""
    return DatabaseServiceError(message)


def _require_mapping(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _unavailable(f"Authoritative {label} data is unavailable.")
    return value


def _require_list(value: Any, label: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise _unavailable(f"Authoritative {label} data is unavailable.")
    if any(not isinstance(item, dict) for item in value):
        raise _unavailable(f"Authoritative {label} data is unavailable.")
    return value


def _require_fields(record: Dict[str, Any], fields: Iterable[str], label: str) -> None:
    if any(field not in record for field in fields):
        raise _unavailable(f"Authoritative {label} data is incomplete.")


def _shift_projection(shift: Dict[str, Any]) -> Dict[str, Any]:
    _require_fields(shift, _SHIFT_FIELDS, "shift")
    return {field: shift[field] for field in _SHIFT_FIELDS}


def _schedule_projection(record: Dict[str, Any]) -> Dict[str, Any]:
    """Project operational schedule facts while excluding every notes field."""
    return {
        "shift_id": record.get("shift_id"),
        "department": record.get("department"),
        "shift_date": record.get("shift_date"),
        "start_time": record.get("start_time"),
        "end_time": record.get("end_time"),
        "required_role": record.get("required_role"),
        "assignment_status": record.get("assignment_status"),
    }


def _assignment_projection(record: Dict[str, Any]) -> Dict[str, Any]:
    _require_fields(
        record,
        ("staff_id", "role", "department", "availability_status",
         "assignment_status"),
        "assignment",
    )
    return {
        "staff_ref": f"staff-{record['staff_id']}",
        "role": record["role"],
        "department": record["department"],
        "availability_status": record["availability_status"],
        "assignment_status": record["assignment_status"],
    }


def _approved_unavailability(candidate: Dict[str, Any]):
    request = candidate.get("approved_request")
    if request is None:
        return None
    if not isinstance(request, dict):
        raise _unavailable("Authoritative candidate data is incomplete.")
    return {
        "start_date": request.get("start_date"),
        "end_date": request.get("end_date"),
        "request_status": request.get("request_status"),
    }


def _risk_indicators(candidate: Dict[str, Any]) -> List[str]:
    """Describe only evidence-derived concerns; do not invent thresholds."""
    risks: List[str] = []
    if not candidate["weekly_ok"]:
        risks.append("Outside recorded recurring weekly availability.")
    if not candidate["department_match"]:
        risks.append("Candidate's home department differs from the shift department.")
    if candidate.get("conflicting_assignment") is not None:
        risks.append("An overlapping active assignment was identified.")
    if candidate.get("approved_request") is not None:
        risks.append("Approved unavailability overlaps the selected shift date.")
    if candidate.get("availability_status") in ("Unavailable", "On Leave"):
        risks.append(
            "Operational availability status is "
            f"{candidate['availability_status']}."
        )
    return risks


def _candidate_projection(candidate: Dict[str, Any], coverage: Dict[str, Any]) -> Dict[str, Any]:
    _require_fields(candidate, _CANDIDATE_REQUIRED_FIELDS, "candidate")
    staff_ref = f"staff-{candidate['staff_id']}"
    current_assignments = _require_list(
        candidate.get("current_assignments"), "candidate schedule")
    conflict = candidate.get("conflicting_assignment")
    if conflict is not None and not isinstance(conflict, dict):
        raise _unavailable("Authoritative candidate conflict data is unavailable.")

    metric_delta = 1 if candidate["eligible"] else 0
    current = coverage["assigned_staff_count"]
    target = coverage["required_staff_count"]
    projected = current + metric_delta
    return {
        "id": staff_ref,
        "label": staff_ref,
        "attributes": {
            "backend_eligible": candidate["eligible"],
            "role": candidate.get("role"),
            "specialisation": candidate.get("specialisation"),
            "department": candidate.get("department"),
            "employment_status": candidate.get("employment_status"),
            "availability_status": candidate.get("availability_status"),
            "department_match": candidate["department_match"],
            "weekly_availability_matches": candidate["weekly_ok"],
            "weekly_rostered_hours": candidate.get("weekly_rostered_hours"),
            "blocked_reason": candidate.get("blocked_reason"),
            "approved_unavailability": _approved_unavailability(candidate),
            "current_assignments": [
                _schedule_projection(row) for row in current_assignments
            ],
            "conflicting_assignment": (
                _schedule_projection(conflict) if conflict is not None else None
            ),
        },
        "outcomes": {
            "metric_delta": metric_delta,
            "projected_assigned_staff_count": projected,
            "projected_staffing_gap": max(target - projected, 0),
        },
        "risk_indicators": _risk_indicators(candidate),
    }


def _coverage_for_shift(shift: Dict[str, Any]) -> Dict[str, Any]:
    result = _require_mapping(
        coverage_service.shift_coverage(
            department=shift["department"],
            shift_date=shift["shift_date"],
            shift_status=shift["shift_status"],
        ),
        "coverage",
    )
    rows = _require_list(result.get("shifts"), "coverage")
    coverage = next(
        (row for row in rows if row.get("shift_id") == shift["shift_id"]), None)
    if coverage is None:
        raise _unavailable("Coverage data for the selected shift is unavailable.")
    _require_fields(coverage, _COVERAGE_FIELDS, "coverage")
    return coverage


def build_recovery_evidence(shift_id: int) -> Dict[str, Any]:
    """Assemble privacy-safe, deterministic evidence for one selected shift."""
    shift = _require_mapping(shift_service.get_shift(shift_id), "shift")
    shift_view = _shift_projection(shift)
    coverage = _coverage_for_shift(shift)

    assignment_rows = _require_list(
        assignment_service.list_shift_assignments(shift_id), "assignment")
    active_assignments = [
        row for row in assignment_rows
        if row.get("assignment_status") not in assignment_service.INACTIVE_STATUSES
    ]
    assignment_views = sorted(
        (_assignment_projection(row) for row in active_assignments),
        key=lambda row: row["staff_ref"],
    )
    if len(assignment_views) != coverage["assigned_staff_count"]:
        raise _unavailable(
            "Assignment data does not match authoritative shift coverage."
        )

    candidate_result = _require_mapping(
        eligibility_service.candidates_for_shift(shift_id), "candidate")
    candidates = _require_list(candidate_result.get("candidates"), "candidate")
    options = [_candidate_projection(row, coverage) for row in candidates]

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_type": EVIDENCE_TYPE,
        "evidence_mode": EVIDENCE_MODE,
        "current_state": {
            "shift": shift_view,
            "coverage": {
                field: coverage[field] for field in _COVERAGE_FIELDS
            },
            "current_assignments": assignment_views,
        },
        "objective": {
            "metric": "assigned_staff",
            "current": coverage["assigned_staff_count"],
            "target": coverage["required_staff_count"],
            "unit": "staff member",
            "staffing_gap": coverage["shortfall"],
        },
        "options": options,
        "constraints": [
            {
                "id": "backend_eligibility",
                "scope": "option",
                "type": "hard",
                "field": "backend_eligible",
                "operator": "equals",
                "value": True,
                "description": (
                    "The existing Student 5 deterministic eligibility service "
                    "must mark the candidate eligible."
                ),
            },
            {
                "id": "weekly_availability_review",
                "scope": "workflow",
                "type": "policy",
                "description": (
                    "Recurring weekly availability is advisory and must be "
                    "reviewed; it is not a configured hard limit."
                ),
            },
            {
                "id": "human_approval",
                "scope": "workflow",
                "type": "approval",
                "description": (
                    "Human approval is required before any assignment change."
                ),
            },
            {
                "id": "no_automatic_assignment",
                "scope": "workflow",
                "type": "safety",
                "description": (
                    "This evidence operation is read-only and cannot execute "
                    "an assignment change."
                ),
            },
        ],
        "unknowns": list(UNKNOWNS),
        "provenance": {
            "sources": [
                {
                    "endpoint": "GET /api/shifts/<shift_id>",
                    "service": "shift_service.get_shift",
                },
                {
                    "endpoint": "GET /api/shifts/coverage",
                    "service": "coverage_service.shift_coverage",
                },
                {
                    "endpoint": "GET /api/shifts/<shift_id>/assignments",
                    "service": "assignment_service.list_shift_assignments",
                },
                {
                    "endpoint": "GET /api/shifts/<shift_id>/candidates",
                    "service": "eligibility_service.candidates_for_shift",
                },
            ],
            "mutation_allowed": False,
        },
    }
