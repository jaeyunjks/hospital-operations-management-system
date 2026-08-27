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


class NotFoundError(BackendError):
    """The requested record does not exist (HTTP 404).

    A subclass of BackendError so existing `except BackendError` handlers keep
    catching it; callers that want a distinct "not found" state check for it
    explicitly first.
    """


class ValidationFailed(BackendError):
    """The backend rejected the submitted values (HTTP 400).

    Like ConflictError, the detail is already phrased for the person who
    submitted the form, so it is surfaced as-is.
    """


class ConflictError(BackendError):
    """The request clashes with existing state (HTTP 409).

    Carries the backend's own explanation unchanged, because that message is
    written for a person to read — "this period overlaps an existing Pending
    request" — and wrapping it in status-code plumbing only obscures it.
    """


class ForbiddenError(BackendError):
    """The simulated role is not permitted to perform this action (HTTP 403).

    Raised when the backend's R0 authorization guard rejects the call. The
    frontend never decides permission for itself — it asks, and renders
    whatever the backend allows. Hiding a button is presentation; this is the
    answer that actually counts.
    """


#: Callable returning the simulated identity headers for the current request,
#: or None. Registered by the frontend app so every backend call carries the
#: caller's simulated role. Kept as a hook rather than a Flask import so this
#: module stays a plain HTTP client with no framework dependency.
_identity_provider = None


def set_identity_provider(provider) -> None:
    """Register a zero-argument callable returning a header dict (or None).

    DEVELOPMENT/DEMO ONLY. These headers carry an unverified, self-asserted
    role for Release 0 demonstration. They are not credentials, they prove
    nothing, and they are not authentication. See backend/authorization.py.
    """
    global _identity_provider
    _identity_provider = provider


def _request(method: str, path: str, params: Optional[Dict[str, Any]] = None,
             payload: Optional[Dict[str, Any]] = None) -> Any:
    url = f"{API_BASE_URL}{path}"

    if params:
        filtered = {key: value for key, value in params.items() if value is not None}
        if filtered:
            url = f"{url}?{urllib.parse.urlencode(filtered)}"

    body = None
    headers = {"Accept": "application/json"}

    # Simulated identity travels with every call so the backend guard, not the
    # template, decides what this role may see.
    if _identity_provider is not None:
        headers.update(_identity_provider() or {})

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
        if error.code == 404:
            raise NotFoundError(detail) from error
        if error.code == 403:
            raise ForbiddenError(detail) from error
        if error.code == 409:
            raise ConflictError(detail) from error
        if error.code == 400:
            # Validation messages are written for the user; pass them through.
            raise ValidationFailed(detail) from error
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
                  department: Optional[str] = None,
                  shift_status: Optional[str] = None) -> Dict[str, Any]:
    """GET /api/shifts/coverage — required vs assigned staffing per shift."""
    return _request("GET", "/api/shifts/coverage",
                     params={"shift_date": shift_date, "department": department,
                             "shift_status": shift_status})


def list_shifts(department: Optional[str] = None,
                shift_date: Optional[str] = None,
                shift_status: Optional[str] = None) -> Dict[str, Any]:
    """GET /api/shifts — shifts, optionally filtered by the real API."""
    return _request("GET", "/api/shifts",
                     params={"department": department, "shift_date": shift_date,
                             "shift_status": shift_status})


def get_shift(shift_id: int) -> Dict[str, Any]:
    """GET /api/shifts/<id> — one shift."""
    return _request("GET", f"/api/shifts/{shift_id}")


def create_shift(payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/shifts — create through the backend service boundary."""
    return _request("POST", "/api/shifts", payload=payload)


def update_shift(shift_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """PUT /api/shifts/<id> — apply the backend's validated update contract."""
    return _request("PUT", f"/api/shifts/{shift_id}", payload=payload)


def delete_shift(shift_id: int) -> Dict[str, Any]:
    """DELETE /api/shifts/<id> — permanently delete a shift."""
    return _request("DELETE", f"/api/shifts/{shift_id}")


def list_shift_assignments(shift_id: int) -> Dict[str, Any]:
    """GET /api/shifts/<id>/assignments — assignment rows plus staff detail."""
    return _request("GET", f"/api/shifts/{shift_id}/assignments")


def list_shift_candidates(shift_id: int) -> Dict[str, Any]:
    """GET /api/shifts/<id>/candidates — staff evaluated against this shift.

    The backend decides eligibility; this layer only renders the verdict.
    Every candidate arrives already carrying ``eligible``, ``blocked_reason``
    and the advisory ``weekly_ok``, in the order the backend chose. The rules
    are deliberately NOT re-implemented here — two copies of a rostering rule
    is two chances for the drawer to offer someone the API would refuse.
    """
    return _request("GET", f"/api/shifts/{shift_id}/candidates")


def suggest_staff(shift_id: int, limit: int = 5) -> Dict[str, Any]:
    """POST /api/shifts/suggest-staff — ranked eligible candidates for a shift.

    The browser never calls the backend itself: this runs server-side like
    every other call in this module, so the simulated identity headers travel
    with it and the backend's manager guard is the thing that decides whether
    the workflow is allowed.

    The reply carries its own provenance — ``mode`` is ``"ai"`` only when the
    model actually ranked the list — and the template renders that verdict
    rather than assuming one. Suggesting is not assigning; nothing here
    creates an assignment.
    """
    return _request("POST", "/api/shifts/suggest-staff",
                     payload={"shift_id": shift_id, "limit": limit})


def assign_staff(shift_id: int, staff_id: int,
                 approved_by: Optional[str] = None) -> Dict[str, Any]:
    """POST /api/shifts/<id>/assign — assign a real numeric staff ID."""
    payload: Dict[str, Any] = {"staff_id": staff_id}
    if approved_by:
        payload["approved_by"] = approved_by
    return _request("POST", f"/api/shifts/{shift_id}/assign", payload=payload)


def unassign_staff(shift_id: int, staff_id: int) -> Dict[str, Any]:
    """PUT /api/shifts/<id>/unassign — cancel the existing assignment."""
    return _request("PUT", f"/api/shifts/{shift_id}/unassign",
                     payload={"staff_id": staff_id})


def list_staff(availability_status: Optional[str] = None,
                department: Optional[str] = None,
                role: Optional[str] = None,
                employment_status: Optional[str] = None) -> Dict[str, Any]:
    """GET /api/staff — staff records, optionally filtered."""
    return _request("GET", "/api/staff",
                     params={"availability_status": availability_status,
                             "department": department,
                             "role": role,
                             "employment_status": employment_status})


def get_staff(staff_id: int) -> Dict[str, Any]:
    """GET /api/staff/<id> — one staff record."""
    return _request("GET", f"/api/staff/{staff_id}")


def list_staff_shifts(staff_id: int) -> Dict[str, Any]:
    """GET /api/staff/<id>/shifts — shifts this staff member is assigned to."""
    return _request("GET", f"/api/staff/{staff_id}/shifts")


def get_weekly_availability(staff_id: int) -> Dict[str, Any]:
    """GET /api/staff/<id>/weekly-availability — recurring weekly pattern."""
    return _request("GET", f"/api/staff/{staff_id}/weekly-availability")


def replace_weekly_availability(staff_id: int, periods) -> Dict[str, Any]:
    """PUT /api/staff/<id>/weekly-availability — replace the whole pattern."""
    return _request("PUT", f"/api/staff/{staff_id}/weekly-availability",
                     payload={"periods": periods})


def update_availability(staff_id: int, availability_status: str) -> Dict[str, Any]:
    """PUT /api/staff/<id>/availability — set operational availability.

    Availability is scheduling state owned by HOMS. Employee reference
    attributes (name, department, employment status) stay read-only here.
    """
    return _request("PUT", f"/api/staff/{staff_id}/availability",
                     payload={"availability_status": availability_status})


def search_staff(query: Optional[str] = None,
                  department: Optional[str] = None,
                  role: Optional[str] = None,
                  availability_status: Optional[str] = None,
                  employment_status: Optional[str] = None) -> Dict[str, Any]:
    """GET /api/staff/search — free-text search plus the same filters.

    The backend rejects a blank `q`, so callers must omit it rather than
    send an empty string.
    """
    return _request("GET", "/api/staff/search",
                     params={"q": query, "department": department,
                             "role": role,
                             "availability_status": availability_status,
                             "employment_status": employment_status})


# --------------------------------------------------------------------------
# Temporary unavailability requests (Scenario C)
# --------------------------------------------------------------------------
# Employee self-service routes are nested under the staff member; the review
# queue is a manager-only collection. That split is the backend's, mirrored
# here so the URL itself says who the call is for.


def list_staff_requests(staff_id: int,
                        request_status: Optional[str] = None) -> Dict[str, Any]:
    """GET /api/staff/<id>/unavailability-requests — an employee's own requests."""
    return _request("GET", f"/api/staff/{staff_id}/unavailability-requests",
                     params={"request_status": request_status})


def create_staff_request(staff_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST /api/staff/<id>/unavailability-requests — submit a new request."""
    return _request("POST", f"/api/staff/{staff_id}/unavailability-requests",
                     payload=payload)


def cancel_staff_request(staff_id: int, request_id: int) -> Dict[str, Any]:
    """PUT /api/staff/<id>/unavailability-requests/<rid>/cancel — withdraw own Pending request."""
    return _request(
        "PUT", f"/api/staff/{staff_id}/unavailability-requests/{request_id}/cancel")


def list_unavailability_requests(request_status: Optional[str] = None,
                                 staff_id: Optional[int] = None) -> Dict[str, Any]:
    """GET /api/unavailability-requests — manager review queue."""
    return _request("GET", "/api/unavailability-requests",
                     params={"request_status": request_status, "staff_id": staff_id})


def get_unavailability_request(request_id: int) -> Dict[str, Any]:
    """GET /api/unavailability-requests/<id> — request plus derived affected shifts.

    `affected_assignments` is calculated from the request dates against live
    assignments each time it is asked for. Nothing about the conflict is
    stored, so it can never go stale against the roster.
    """
    return _request("GET", f"/api/unavailability-requests/{request_id}")


def review_unavailability_request(request_id: int, decision: str,
                                  reviewed_by: str) -> Dict[str, Any]:
    """PUT /api/unavailability-requests/<id>/review — approve or reject.

    Records the decision only. It does not unassign anybody, and it does not
    touch staff.availability_status; acting on the roster stays a separate,
    deliberate manager action.
    """
    return _request("PUT", f"/api/unavailability-requests/{request_id}/review",
                     payload={"decision": decision, "reviewed_by": reviewed_by})


#: Reasons offered in the submission form. Free text is still accepted by the
#: backend; this list only shapes the UI.
REQUEST_REASONS = ("Personal", "Vacation", "Study leave", "Medical", "Other")

#: Lifecycle states of a temporary unavailability request.
REQUEST_STATUSES = ("Pending", "Approved", "Rejected", "Cancelled")


#: Values accepted by PUT /api/staff/<id>/availability, mirroring the
#: CHECK constraint on staff.availability_status.
AVAILABILITY_STATUSES = ("Available", "Unavailable", "On Leave")
EMPLOYMENT_STATUSES = ("Full-Time", "Part-Time", "Casual", "Contract")
SHIFT_STATUSES = ("Planned", "Open", "Filled", "Completed", "Cancelled")


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
