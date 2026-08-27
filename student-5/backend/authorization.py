"""Role-based authorization for the Student 5 backend/API microservice.

Release 0 SECURITY LIMITATION — read this before relying on it
-------------------------------------------------------------
The caller's identity arrives as request headers (``X-HOMS-Role`` and
``X-HOMS-Staff-Id``) supplied by the frontend service. That is a **simulated**
identity for development and demonstration: nothing here verifies that the
caller really is who the headers claim, so these guards are not a substitute
for authentication and must not be described as such. Any client able to reach
this service directly can set the headers freely.

What the guards DO provide is the permission model itself — who may do what,
enforced server-side rather than by hiding buttons in the UI. When the shared
HOMS authentication service arrives in a later release, only
``identity_from_request`` changes: it should derive the role and staff id from
a verified token instead of headers. Every ``require_*`` call site, and every
rule expressed below, stays exactly as written.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from flask import request

from errors import ApiError

ROLE_MANAGER = "Staff Manager"
ROLE_EMPLOYEE = "Employee"
ROLES = (ROLE_MANAGER, ROLE_EMPLOYEE)

ROLE_HEADER = "X-HOMS-Role"
STAFF_ID_HEADER = "X-HOMS-Staff-Id"


class ForbiddenError(ApiError):
    """The caller's role does not permit this operation."""

    status_code = 403
    error_code = "forbidden"


def identity_from_request() -> Dict[str, Any]:
    """Return the caller's simulated identity.

    THIS IS THE ONLY FUNCTION A REAL AUTHENTICATION SERVICE NEEDS TO REPLACE.
    It should then read a verified token rather than trusting headers.

    Requests arriving with no role header are treated as Staff Manager so the
    existing manager-facing endpoints and tests keep working unchanged; that
    default is itself part of the Release 0 simulation.
    """
    role = request.headers.get(ROLE_HEADER) or ROLE_MANAGER
    if role not in ROLES:
        role = ROLE_MANAGER

    raw_staff_id = request.headers.get(STAFF_ID_HEADER)
    try:
        staff_id = int(raw_staff_id) if raw_staff_id else None
    except (TypeError, ValueError):
        staff_id = None

    return {"role": role, "staff_id": staff_id}


def require_manager() -> Dict[str, Any]:
    """Workforce-wide operations: directory, shifts, assignment, review."""
    identity = identity_from_request()
    if identity["role"] != ROLE_MANAGER:
        raise ForbiddenError(
            "This operation requires the Staff Manager role.",
            {"role": identity["role"]})
    return identity


def require_self_or_manager(staff_id: int) -> Dict[str, Any]:
    """Employee self-service data.

    A manager may read any employee's record; an employee may only reach
    their own. This is the check that stops one employee reading another's
    shifts, availability or requests.
    """
    identity = identity_from_request()
    if identity["role"] == ROLE_MANAGER:
        return identity
    if identity["staff_id"] != staff_id:
        raise ForbiddenError(
            "Employees may only access their own workforce data.",
            {"role": identity["role"], "requested_staff_id": staff_id})
    return identity


def require_self(staff_id: int) -> Dict[str, Any]:
    """Actions only the employee themselves may take (create/cancel own request).

    A manager is deliberately NOT permitted here: submitting or cancelling a
    request on someone else's behalf is a different operation, and Release 0
    does not model it.
    """
    identity = identity_from_request()
    if identity["staff_id"] != staff_id:
        raise ForbiddenError(
            "Only the employee can submit or cancel their own request.",
            {"role": identity["role"], "requested_staff_id": staff_id})
    return identity
