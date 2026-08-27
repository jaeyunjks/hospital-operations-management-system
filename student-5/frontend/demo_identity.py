"""Simulated identity for the Release 0 demonstration. NOT AUTHENTICATION.

WHAT THIS IS
    A way to demonstrate role-based behaviour without a login system. The
    person using the demo states who they are, and the application takes them
    at their word. That choice is kept in the Flask session cookie and sent to
    the backend as the `X-HOMS-Role` / `X-HOMS-Staff-Id` headers.

WHAT THIS IS NOT
    It is not authentication, and it is not a security control. Nothing is
    verified. There is no password, no credential, no proof of identity, and
    nothing stopping anyone from claiming any role. Anybody able to reach the
    backend directly can send those headers themselves.

    The single reason this is acceptable for Release 0 is that the permission
    DECISION lives on the server, in backend/authorization.py, not in these
    templates. Hiding a button here changes what is easy to click; it does not
    change what is permitted. Both layers exist, and only the backend one
    counts.

WHEN AUTHENTICATION ARRIVES
    The shared HOMS authentication service replaces the entry screen and this
    module's `identity_headers()` with a verified session. Every permission
    check downstream stays exactly as written, because none of them ask how
    the identity was established — only what it is.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from flask import session

#: Role names, matching backend/authorization.py exactly. A typo here would
#: silently downgrade someone to "not a manager", so they are shared constants
#: rather than string literals scattered through the templates.
ROLE_MANAGER = "Staff Manager"
ROLE_EMPLOYEE = "Employee"
ROLES = (ROLE_MANAGER, ROLE_EMPLOYEE)

#: Headers the backend reads. Unverified by design — see the module docstring.
ROLE_HEADER = "X-HOMS-Role"
STAFF_ID_HEADER = "X-HOMS-Staff-Id"

#: Session key holding the simulated identity.
SESSION_KEY = "homs_demo_identity"


def current_identity() -> Optional[Dict[str, Any]]:
    """The simulated identity for this request, or None if none was chosen."""
    identity = session.get(SESSION_KEY)
    if not isinstance(identity, dict) or identity.get("role") not in ROLES:
        return None
    if identity["role"] == ROLE_EMPLOYEE and not identity.get("staff_id"):
        # An employee identity without a staff member is meaningless — every
        # employee route is scoped to a specific person.
        return None
    return identity


def set_identity(role: str, staff_id: Optional[int] = None,
                 name: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    """Record the chosen demo identity in the session."""
    identity: Dict[str, Any] = {"role": role, "staff_id": staff_id, "name": name}
    identity.update(extra)
    session[SESSION_KEY] = identity
    return identity


def clear_identity() -> None:
    """Leave the demo identity — returns the user to the entry screen."""
    session.pop(SESSION_KEY, None)


def is_manager() -> bool:
    identity = current_identity()
    return bool(identity and identity["role"] == ROLE_MANAGER)


def is_employee() -> bool:
    identity = current_identity()
    return bool(identity and identity["role"] == ROLE_EMPLOYEE)


def current_staff_id() -> Optional[int]:
    """The staff member an employee identity is acting as, else None."""
    identity = current_identity()
    return identity.get("staff_id") if identity else None


def identity_headers() -> Dict[str, str]:
    """Headers for api_client to attach to every backend call.

    Returns an empty dict outside a request context or before a role is
    chosen, in which case the backend applies its own default.
    """
    try:
        identity = current_identity()
    except RuntimeError:  # no request context (e.g. a background call)
        return {}
    if not identity:
        return {}

    headers = {ROLE_HEADER: identity["role"]}
    if identity.get("staff_id") is not None:
        headers[STAFF_ID_HEADER] = str(identity["staff_id"])
    return headers
