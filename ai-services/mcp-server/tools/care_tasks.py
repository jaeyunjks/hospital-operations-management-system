"""Read-only access to Student 2's published clinical-record and care-task APIs."""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any, Dict, List, Set
from urllib.parse import urlsplit

import httpx

TOOL_NAME = "homs_open_care_tasks"
DEFAULT_API_URL = "http://127.0.0.1:5200/api"
DEFAULT_API_TIMEOUT = 10.0
# The admission-history route is doctor-only and care-task reads accept doctors,
# so "doctor" is the one role that can make both calls. It is never caller-chosen.
CLINICAL_ROLE = "doctor"
ROLE_HEADER = "X-User-Role"
OPEN_STATUSES = ("pending", "acknowledged")


def _api_url(name: str, default: str) -> str:
    """Read the operator-configured Student 2 API base URL."""

    api_url = os.environ.get(name, default).strip().rstrip("/")
    try:
        parsed = urlsplit(api_url)
        valid_url = (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            valid_url = False
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError(
            f"{name} must be an HTTP(S) API base URL without credentials, query or fragment"
        )
    return api_url


def _api_timeout(name: str, default: float) -> float:
    """Read the bounded upstream deadline in seconds."""

    try:
        api_timeout = float(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(api_timeout) or not 0.1 <= api_timeout <= 30:
        raise ValueError(f"{name} must be between 0.1 and 30 seconds")
    return api_timeout


API_URL = _api_url("HOMS_STUDENT2_API_URL", DEFAULT_API_URL)
API_TIMEOUT = _api_timeout("HOMS_STUDENT2_API_TIMEOUT", DEFAULT_API_TIMEOUT)

_TASK_PROPERTIES = {
    "task_id": {"type": "integer", "minimum": 1},
    "description": {"type": "string", "minLength": 1},
    "status": {"enum": list(OPEN_STATUSES)},
    "due_at": {"type": ["string", "null"]},
}
DATA_SCHEMA = {
    "type": "object",
    "properties": {
        "admission_id": {"type": "integer", "minimum": 1},
        "count": {"type": "integer", "minimum": 0},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _TASK_PROPERTIES,
                "required": list(_TASK_PROPERTIES),
                "additionalProperties": False,
            },
        },
    },
    "required": ["admission_id", "count", "tasks"],
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


class _UpstreamFailure(Exception):
    """A classified Student 2 API failure; carries a reason, never upstream text."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def tool_error(
    code: str, message: str, details: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """Use the existing schema-versioned MCP error envelope, without upstream text."""

    return {
        "schema_version": "1.0",
        "ok": False,
        "tool": TOOL_NAME,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def _upstream_error(reason: str) -> Dict[str, Any]:
    message = (
        "Student 2 clinical API timed out."
        if reason == "timeout"
        else "Student 2 clinical API is unavailable."
    )
    return tool_error("upstream_unavailable", message, {"reason": reason})


def _client(timeout: float) -> httpx.AsyncClient:
    # Neither caller-controlled destinations nor redirects/proxies may widen access.
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)


async def _get_json(
    client: httpx.AsyncClient, url: str, params: Dict[str, Any] | None = None
) -> object:
    """The only network call in this module: one GET with the fixed role header."""

    response = await client.get(
        url,
        params=params,
        headers={"Accept": "application/json", ROLE_HEADER: CLINICAL_ROLE},
    )
    status = response.status_code
    if status in (408, 504):
        raise _UpstreamFailure("timeout")
    if status >= 500:
        raise _UpstreamFailure("server_error")
    if status in (401, 403):
        raise _UpstreamFailure("access_denied")
    if status != 200:
        raise _UpstreamFailure("unexpected_status")
    return response.json()


def _record_ids(body: object, admission_id: int) -> Set[int]:
    """Keep only clinical record ids; every other record field is discarded here."""

    if (
        not isinstance(body, dict)
        or body.get("admission_id") != admission_id
        or not isinstance(body.get("clinical_records"), list)
    ):
        raise ValueError("Invalid clinical-record history")
    record_ids = set()
    for row in body["clinical_records"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid clinical record")
        record_id = row.get("record_id")
        if type(record_id) is not int or row.get("admission_id") != admission_id:
            raise ValueError("Invalid or foreign clinical record")
        record_ids.add(record_id)
    return record_ids


def _open_tasks(body: object, record_ids: Set[int]) -> List[Dict[str, Any]]:
    """Validate, then construct a strict field allowlist (no notes, no nurse ids)."""

    if not isinstance(body, dict) or not isinstance(body.get("care_tasks"), list):
        raise ValueError("Invalid care-task list")
    tasks = []
    for row in body["care_tasks"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid care task")
        if row.get("clinical_record_id") not in record_ids:
            continue
        if row.get("status") not in OPEN_STATUSES:
            continue
        task_id = row.get("task_id")
        description = row.get("task_description")
        due_at = row.get("due_at")
        if (
            type(task_id) is not int
            or task_id < 1
            or not isinstance(description, str)
            or not description.strip()
            or (due_at is not None and not isinstance(due_at, str))
        ):
            raise ValueError("Invalid care task fields")
        tasks.append(
            {
                "task_id": task_id,
                "description": description,
                "status": row["status"],
                "due_at": due_at,
            }
        )
    tasks.sort(key=lambda task: task["task_id"])
    return tasks


async def homs_open_care_tasks(
    admission_id: object = None, *, api_url: str, timeout: float
) -> Dict[str, Any]:
    """List pending and acknowledged care tasks for one admission; GET requests only."""

    if admission_id is None:
        return tool_error(
            "invalid_input",
            "'admission_id' is required.",
            {"field": "admission_id", "reason": "missing"},
        )
    if type(admission_id) is not int:
        return tool_error(
            "invalid_input",
            "'admission_id' must be a whole number.",
            {
                "field": "admission_id",
                "reason": "wrong_type",
                "received_type": type(admission_id).__name__,
            },
        )
    if admission_id < 1:
        return tool_error(
            "invalid_input",
            "'admission_id' must be a positive whole number.",
            {"field": "admission_id", "reason": "not_positive"},
        )

    base_url = api_url.rstrip("/")
    try:
        # Bound both calls together as well as httpx's individual I/O stages.
        async with asyncio.timeout(timeout):
            async with _client(timeout) as client:
                history = await _get_json(
                    client, f"{base_url}/clinical-records/admission/{admission_id}"
                )
                record_ids = _record_ids(history, admission_id)
                if not record_ids:
                    return tool_error(
                        "not_found",
                        "No clinical records were found for this admission.",
                        {"field": "admission_id"},
                    )
                # Trailing slash matters: redirects are not followed.
                listing = await _get_json(
                    client, f"{base_url}/care-tasks/", {"admission_id": admission_id}
                )
                tasks = _open_tasks(listing, record_ids)
    except _UpstreamFailure as failure:
        return _upstream_error(failure.reason)
    except (TimeoutError, httpx.TimeoutException):
        return _upstream_error("timeout")
    except httpx.RequestError:
        return _upstream_error("unreachable")
    except (ValueError, TypeError, OverflowError):
        return _upstream_error("invalid_response")

    return {
        "schema_version": "1.0",
        "ok": True,
        "tool": TOOL_NAME,
        "data": {"admission_id": admission_id, "count": len(tasks), "tasks": tasks},
        "error": None,
    }
