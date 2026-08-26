"""Server-side client for the Student 5 backend/API microservice.

Staff & Shift Management — frontend layer. This module is the ONLY place the
frontend talks to the backend. Every call happens server-side (Flask process
to Flask process), so the browser never calls the backend directly and CORS
never enters into it.

Uses the standard library only (`urllib`), matching the pattern already used
by student-5/backend/database_client.py, so this service adds no dependency
beyond Flask.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

#: Base URL of the Staff & Shift backend/API microservice (real port: 5500).
API_BASE_URL = os.environ.get("STAFF_SHIFT_API_URL", "http://127.0.0.1:5500")

#: Seconds to wait before giving up on the backend.
API_TIMEOUT = float(os.environ.get("STAFF_SHIFT_API_TIMEOUT", "5"))


class BackendUnavailableError(Exception):
    """The backend/API microservice could not be reached at all."""


class BackendError(Exception):
    """The backend/API microservice responded, but with an error or bad body."""


def _request(method: str, path: str, params: Optional[Dict[str, Any]] = None,
             payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{API_BASE_URL}{path}"

    if params:
        filtered = {key: value for key, value in params.items() if value is not None}
        if filtered:
            url = f"{url}?{urllib.parse.urlencode(filtered)}"

    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None

    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8")).get("message", error.reason)
        except Exception:
            detail = str(error.reason)
        raise BackendError(f"Staff & Shift service returned {error.code}: {detail}") from error

    except urllib.error.URLError as error:
        raise BackendUnavailableError(
            "Workforce data is temporarily unavailable. "
            "Check that the Staff & Shift service is running."
        ) from error

    except json.JSONDecodeError as error:
        raise BackendError("Staff & Shift service returned an unreadable response.") from error


def get_health() -> Dict[str, Any]:
    """GET /health — liveness of the backend/API microservice."""
    return _request("GET", "/health")


def get_coverage(shift_date: Optional[str] = None,
                  department: Optional[str] = None) -> Dict[str, Any]:
    """GET /api/shifts/coverage — required vs assigned staffing per shift."""
    return _request("GET", "/api/shifts/coverage",
                     params={"shift_date": shift_date, "department": department})


def list_staff(availability_status: Optional[str] = None,
                department: Optional[str] = None) -> Dict[str, Any]:
    """GET /api/staff — staff records, optionally filtered."""
    return _request("GET", "/api/staff",
                     params={"availability_status": availability_status,
                             "department": department})


def get_coverage_summary(shift_date: Optional[str] = None,
                          department: Optional[str] = None) -> Dict[str, Any]:
    """POST /api/shifts/coverage-summary — coverage shaped for summarisation.

    Existing endpoint (student-5/backend/routes/ai_routes.py). Currently
    rule-based (`ai_enabled: false`, `mode: "rule-based"`) — it is real,
    already-implemented data, not an AI recommendation.
    """
    payload: Dict[str, Any] = {}
    if shift_date:
        payload["shift_date"] = shift_date
    if department:
        payload["department"] = department
    return _request("POST", "/api/shifts/coverage-summary", payload=payload)
