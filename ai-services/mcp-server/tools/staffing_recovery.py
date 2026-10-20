"""Read-only access to Student 5's published staffing-recovery evidence API."""

from __future__ import annotations

import asyncio
import copy
import math
import re
from typing import Any, Dict, Iterable

import httpx


TOOL_NAME = "homs_staffing_recovery_context"
SOURCE = "student-5-recovery-evidence-api"
ROLE_HEADER = "X-HOMS-Role"
MANAGER_ROLE = "Staff Manager"
_STAFF_REF = re.compile(r"^staff-[1-9][0-9]*$")

SHIFT_FIELDS = {
    "shift_id", "department", "shift_date", "start_time", "end_time",
    "required_role", "required_staff_count", "shift_status",
}
COVERAGE_FIELDS = {
    "required_staff_count", "assigned_staff_count", "filled_staff_count",
    "shortfall", "surplus", "coverage_status",
}
ASSIGNMENT_FIELDS = {
    "staff_ref", "role", "department", "availability_status",
    "assignment_status",
}
SCHEDULE_FIELDS = {
    "shift_id", "department", "shift_date", "start_time", "end_time",
    "required_role", "assignment_status",
}
ATTRIBUTES_FIELDS = {
    "backend_eligible", "role", "specialisation", "department",
    "employment_status", "availability_status", "department_match",
    "weekly_availability_matches", "weekly_rostered_hours", "blocked_reason",
    "approved_unavailability", "current_assignments", "conflicting_assignment",
}
OUTCOME_FIELDS = {
    "metric_delta", "projected_assigned_staff_count", "projected_staffing_gap",
}
OPTION_FIELDS = {"id", "label", "attributes", "outcomes", "risk_indicators"}
CONSTRAINT_FIELDS = {
    "id", "scope", "type", "field", "operator", "value", "description",
}
EVIDENCE_FIELDS = {
    "schema_version", "evidence_type", "evidence_mode", "current_state",
    "objective", "options", "constraints", "unknowns", "provenance",
}


def _nullable_string_schema() -> Dict[str, Any]:
    return {"type": ["string", "null"]}


SCHEDULE_SCHEMA = {
    "type": "object",
    "properties": {
        "shift_id": {"type": "integer", "minimum": 1},
        "department": _nullable_string_schema(),
        "shift_date": _nullable_string_schema(),
        "start_time": _nullable_string_schema(),
        "end_time": _nullable_string_schema(),
        "required_role": _nullable_string_schema(),
        "assignment_status": _nullable_string_schema(),
    },
    "required": sorted(SCHEDULE_FIELDS),
    "additionalProperties": False,
}

EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"const": "1.0"},
        "evidence_type": {"const": "student5_staffing_recovery"},
        "evidence_mode": {"const": "live_api"},
        "current_state": {
            "type": "object",
            "properties": {
                "shift": {
                    "type": "object",
                    "properties": {
                        "shift_id": {"type": "integer", "minimum": 1},
                        "department": {"type": "string", "minLength": 1},
                        "shift_date": {"type": "string", "minLength": 1},
                        "start_time": {"type": "string", "minLength": 1},
                        "end_time": {"type": "string", "minLength": 1},
                        "required_role": {"type": "string", "minLength": 1},
                        "required_staff_count": {"type": "integer", "minimum": 0},
                        "shift_status": {"type": "string", "minLength": 1},
                    },
                    "required": sorted(SHIFT_FIELDS),
                    "additionalProperties": False,
                },
                "coverage": {
                    "type": "object",
                    "properties": {
                        field: {"type": "integer", "minimum": 0}
                        for field in COVERAGE_FIELDS - {"coverage_status"}
                    } | {"coverage_status": {"type": "string", "minLength": 1}},
                    "required": sorted(COVERAGE_FIELDS),
                    "additionalProperties": False,
                },
                "current_assignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "staff_ref": {"type": "string", "pattern": r"^staff-[1-9][0-9]*$"},
                            "role": {"type": "string", "minLength": 1},
                            "department": {"type": "string", "minLength": 1},
                            "availability_status": {"type": "string", "minLength": 1},
                            "assignment_status": {"type": "string", "minLength": 1},
                        },
                        "required": sorted(ASSIGNMENT_FIELDS),
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["shift", "coverage", "current_assignments"],
            "additionalProperties": False,
        },
        "objective": {
            "type": "object",
            "properties": {
                "metric": {"const": "assigned_staff"},
                "current": {"type": "integer", "minimum": 0},
                "target": {"type": "integer", "minimum": 0},
                "unit": {"const": "staff member"},
                "staffing_gap": {"type": "integer", "minimum": 0},
            },
            "required": ["metric", "current", "target", "unit", "staffing_gap"],
            "additionalProperties": False,
        },
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": r"^staff-[1-9][0-9]*$"},
                    "label": {"type": "string", "pattern": r"^staff-[1-9][0-9]*$"},
                    "attributes": {
                        "type": "object",
                        "properties": {
                            "backend_eligible": {"type": "boolean"},
                            "role": _nullable_string_schema(),
                            "specialisation": _nullable_string_schema(),
                            "department": _nullable_string_schema(),
                            "employment_status": _nullable_string_schema(),
                            "availability_status": _nullable_string_schema(),
                            "department_match": {"type": "boolean"},
                            "weekly_availability_matches": {"type": "boolean"},
                            "weekly_rostered_hours": {"type": ["number", "null"], "minimum": 0},
                            "blocked_reason": _nullable_string_schema(),
                            "approved_unavailability": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "start_date": _nullable_string_schema(),
                                            "end_date": _nullable_string_schema(),
                                            "request_status": _nullable_string_schema(),
                                        },
                                        "required": ["start_date", "end_date", "request_status"],
                                        "additionalProperties": False,
                                    },
                                ]
                            },
                            "current_assignments": {"type": "array", "items": SCHEDULE_SCHEMA},
                            "conflicting_assignment": {
                                "anyOf": [{"type": "null"}, SCHEDULE_SCHEMA]
                            },
                        },
                        "required": sorted(ATTRIBUTES_FIELDS),
                        "additionalProperties": False,
                    },
                    "outcomes": {
                        "type": "object",
                        "properties": {
                            field: {"type": "integer", "minimum": 0}
                            for field in OUTCOME_FIELDS
                        },
                        "required": sorted(OUTCOME_FIELDS),
                        "additionalProperties": False,
                    },
                    "risk_indicators": {"type": "array", "items": {"type": "string"}},
                },
                "required": sorted(OPTION_FIELDS),
                "additionalProperties": False,
            },
        },
        "constraints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "scope": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "minLength": 1},
                    "field": {"type": "string", "minLength": 1},
                    "operator": {"type": "string", "minLength": 1},
                    "value": {},
                    "description": {"type": "string", "minLength": 1},
                },
                "required": ["id", "scope", "type", "description"],
                "additionalProperties": False,
            },
        },
        "unknowns": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "provenance": {
            "type": "object",
            "properties": {
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "endpoint": {"type": "string", "minLength": 1},
                            "service": {"type": "string", "minLength": 1},
                        },
                        "required": ["endpoint", "service"],
                        "additionalProperties": False,
                    },
                },
                "mutation_allowed": {"const": False},
            },
            "required": ["sources", "mutation_allowed"],
            "additionalProperties": False,
        },
    },
    "required": sorted(EVIDENCE_FIELDS),
    "additionalProperties": False,
}

DATA_SCHEMA = {
    "type": "object",
    "properties": {
        "shift_id": {"type": "integer", "minimum": 1},
        "evidence": EVIDENCE_SCHEMA,
        "source": {"const": SOURCE},
        "mutation_allowed": {"const": False},
    },
    "required": ["shift_id", "evidence", "source", "mutation_allowed"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"const": "1.0"},
        "ok": {"type": "boolean"},
        "tool": {"const": TOOL_NAME},
        "data": {"anyOf": [DATA_SCHEMA, {"type": "null"}]},
        "error": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "details": {"type": "object"},
                    },
                    "required": ["code", "message", "details"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["schema_version", "ok", "tool", "data", "error"],
    "additionalProperties": False,
}


def tool_error(
    code: str, message: str, details: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0",
        "ok": False,
        "tool": TOOL_NAME,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)


def _mapping(value: Any, fields: Iterable[str]) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Invalid object shape")
    return value


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _optional_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _validate_schedule(value: Any) -> None:
    row = _mapping(value, SCHEDULE_FIELDS)
    if type(row["shift_id"]) is not int or row["shift_id"] < 1:
        raise ValueError("Invalid schedule shift")
    if not all(_optional_string(row[field]) for field in SCHEDULE_FIELDS - {"shift_id"}):
        raise ValueError("Invalid schedule field")


def _validate_evidence(body: Any, shift_id: int) -> Dict[str, Any]:
    evidence = _mapping(body, EVIDENCE_FIELDS)
    if (
        evidence["schema_version"] != "1.0"
        or evidence["evidence_type"] != "student5_staffing_recovery"
        or evidence["evidence_mode"] != "live_api"
    ):
        raise ValueError("Invalid evidence identity")

    current = _mapping(
        evidence["current_state"], {"shift", "coverage", "current_assignments"}
    )
    shift = _mapping(current["shift"], SHIFT_FIELDS)
    if shift["shift_id"] != shift_id:
        raise ValueError("Wrong shift")
    descriptive_shift_fields = SHIFT_FIELDS - {"shift_id", "required_staff_count"}
    if not all(_nonblank(shift[field]) for field in descriptive_shift_fields):
        raise ValueError("Invalid shift")
    if not _count(shift["required_staff_count"]):
        raise ValueError("Invalid required count")

    coverage = _mapping(current["coverage"], COVERAGE_FIELDS)
    if not all(_count(coverage[field]) for field in COVERAGE_FIELDS - {"coverage_status"}):
        raise ValueError("Invalid coverage")
    if not _nonblank(coverage["coverage_status"]):
        raise ValueError("Invalid coverage status")
    if coverage["filled_staff_count"] != min(
        coverage["assigned_staff_count"], coverage["required_staff_count"]
    ):
        raise ValueError("Inconsistent filled count")
    if coverage["shortfall"] != max(
        coverage["required_staff_count"] - coverage["assigned_staff_count"], 0
    ) or coverage["surplus"] != max(
        coverage["assigned_staff_count"] - coverage["required_staff_count"], 0
    ):
        raise ValueError("Inconsistent coverage")

    assignments = current["current_assignments"]
    if not isinstance(assignments, list):
        raise ValueError("Invalid assignments")
    for assignment in assignments:
        row = _mapping(assignment, ASSIGNMENT_FIELDS)
        if not _STAFF_REF.fullmatch(row["staff_ref"] if isinstance(row["staff_ref"], str) else ""):
            raise ValueError("Invalid assignment reference")
        if not all(_nonblank(row[field]) for field in ASSIGNMENT_FIELDS - {"staff_ref"}):
            raise ValueError("Invalid assignment")
    if len(assignments) != coverage["assigned_staff_count"]:
        raise ValueError("Assignment count mismatch")

    objective = _mapping(
        evidence["objective"], {"metric", "current", "target", "unit", "staffing_gap"}
    )
    if (
        objective["metric"] != "assigned_staff"
        or objective["unit"] != "staff member"
        or not all(_count(objective[field]) for field in ("current", "target", "staffing_gap"))
        or objective["current"] != coverage["assigned_staff_count"]
        or objective["target"] != coverage["required_staff_count"]
        or objective["staffing_gap"] != coverage["shortfall"]
    ):
        raise ValueError("Invalid objective")

    options = evidence["options"]
    if not isinstance(options, list):
        raise ValueError("Invalid options")
    for option in options:
        row = _mapping(option, OPTION_FIELDS)
        if (
            not isinstance(row["id"], str)
            or not _STAFF_REF.fullmatch(row["id"])
            or row["label"] != row["id"]
        ):
            raise ValueError("Invalid option identity")
        attributes = _mapping(row["attributes"], ATTRIBUTES_FIELDS)
        if any(type(attributes[field]) is not bool for field in (
            "backend_eligible", "department_match", "weekly_availability_matches"
        )):
            raise ValueError("Invalid option flags")
        for field in (
            "role", "specialisation", "department", "employment_status",
            "availability_status", "blocked_reason",
        ):
            if not _optional_string(attributes[field]):
                raise ValueError("Invalid option attribute")
        hours = attributes["weekly_rostered_hours"]
        if hours is not None and (
            type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0
        ):
            raise ValueError("Invalid rostered hours")
        approved = attributes["approved_unavailability"]
        if approved is not None:
            approved = _mapping(approved, {"start_date", "end_date", "request_status"})
            if not all(_optional_string(value) for value in approved.values()):
                raise ValueError("Invalid unavailability")
        schedules = attributes["current_assignments"]
        if not isinstance(schedules, list):
            raise ValueError("Invalid candidate schedule")
        for schedule in schedules:
            _validate_schedule(schedule)
        if attributes["conflicting_assignment"] is not None:
            _validate_schedule(attributes["conflicting_assignment"])
        outcomes = _mapping(row["outcomes"], OUTCOME_FIELDS)
        if not all(_count(outcomes[field]) for field in OUTCOME_FIELDS):
            raise ValueError("Invalid option outcome")
        if outcomes["metric_delta"] != (1 if attributes["backend_eligible"] else 0):
            raise ValueError("Inconsistent option outcome")
        if not isinstance(row["risk_indicators"], list) or any(
            not isinstance(item, str) for item in row["risk_indicators"]
        ):
            raise ValueError("Invalid risks")

    constraints = evidence["constraints"]
    if not isinstance(constraints, list):
        raise ValueError("Invalid constraints")
    for constraint in constraints:
        if (
            not isinstance(constraint, dict)
            or not {"id", "scope", "type", "description"}.issubset(constraint)
            or not set(constraint).issubset(CONSTRAINT_FIELDS)
            or not all(
                _nonblank(constraint[field])
                for field in ("id", "scope", "type", "description")
            )
        ):
            raise ValueError("Invalid constraint")
    if not any(
        item["id"] == "human_approval" and item["type"] == "approval"
        for item in constraints
    ):
        raise ValueError("Missing approval boundary")

    if not isinstance(evidence["unknowns"], list) or any(
        not _nonblank(item) for item in evidence["unknowns"]
    ):
        raise ValueError("Invalid unknowns")
    provenance = _mapping(evidence["provenance"], {"sources", "mutation_allowed"})
    if provenance["mutation_allowed"] is not False or not isinstance(provenance["sources"], list):
        raise ValueError("Invalid provenance")
    for source in provenance["sources"]:
        source = _mapping(source, {"endpoint", "service"})
        if not _nonblank(source["endpoint"]) or not _nonblank(source["service"]):
            raise ValueError("Invalid source")

    return copy.deepcopy(evidence)


async def homs_staffing_recovery_context(
    shift_id: object = None, *, api_url: str, timeout: float
) -> Dict[str, Any]:
    """Fetch one validated evidence snapshot through one fixed GET operation."""

    if shift_id is None:
        return tool_error(
            "invalid_input", "'shift_id' is required.",
            {"field": "shift_id", "reason": "missing"},
        )
    if type(shift_id) is not int:
        return tool_error(
            "invalid_input", "'shift_id' must be a whole number.",
            {
                "field": "shift_id",
                "reason": "wrong_type",
                "received_type": type(shift_id).__name__,
            },
        )
    if shift_id < 1:
        return tool_error(
            "invalid_input", "'shift_id' must be a positive whole number.",
            {"field": "shift_id", "reason": "not_positive"},
        )

    try:
        async with asyncio.timeout(timeout):
            async with _client(timeout) as client:
                response = await client.get(
                    f"{api_url.rstrip('/')}/shifts/{shift_id}/recovery-evidence",
                    headers={"Accept": "application/json", ROLE_HEADER: MANAGER_ROLE},
                )
        if response.status_code == 404:
            return tool_error(
                "not_found", "The selected Student 5 shift was not found.",
                {"field": "shift_id"},
            )
        if response.status_code in (408, 504):
            return tool_error("upstream_timeout", "Student 5 recovery API timed out.")
        if response.status_code >= 500:
            return tool_error("upstream_unavailable", "Student 5 recovery API is unavailable.")
        if response.status_code != 200:
            return tool_error(
                "upstream_invalid_response", "Student 5 recovery API returned an invalid response."
            )
        evidence = _validate_evidence(response.json(), shift_id)
    except (TimeoutError, httpx.TimeoutException):
        return tool_error("upstream_timeout", "Student 5 recovery API timed out.")
    except httpx.RequestError:
        return tool_error("upstream_unavailable", "Student 5 recovery API is unavailable.")
    except (ValueError, TypeError, OverflowError):
        return tool_error(
            "upstream_invalid_response", "Student 5 recovery API returned an invalid response."
        )

    return {
        "schema_version": "1.0",
        "ok": True,
        "tool": TOOL_NAME,
        "data": {
            "shift_id": shift_id,
            "evidence": evidence,
            "source": SOURCE,
            "mutation_allowed": False,
        },
        "error": None,
    }
