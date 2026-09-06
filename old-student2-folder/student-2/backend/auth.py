"""
auth.py - Temporary authentication stand-in for the Clinical Staff Management service.

WHAT THIS IS
------------
This file is a placeholder. There is no real login, no password check, no token
verification here. It exists so the rest of the service can be built and tested
against a "who is calling this endpoint?" question before the shared
authentication component (owned by another team) is ready to plug in.

When the real auth arrives, everything below gets replaced: CURRENT_USER becomes
"decode the request's identity from the shared auth layer", and the two
decorators start reading that identity instead of this module-level dict. The
route code that uses the decorators should not need to change.

HOW IT WORKS RIGHT NOW
----------------------
1. CURRENT_USER is a single hardcoded dict (id / name / role). To test the
   service as a different person, edit this dict by hand and restart. A few
   ready-made users from the seed data are listed in _SAMPLE_USERS below - copy
   one into CURRENT_USER.

2. @require_role("doctor", "nurse", ...) guards an endpoint by role. This service
   only ever serves clinical staff, so any role that is not doctor / nurse /
   specialist is rejected for every endpoint - including a call with no role or
   an unknown role. Call it with no arguments to mean "any clinical role".

3. @require_assignment(lookup) guards an endpoint by record ownership. You pass a
   function that, given the same *args/**kwargs the view receives, returns the
   staff id assigned to that resource (e.g. care_task.assigned_nurse_id). The
   decorator compares it to the current user's id. This is what stops a doctor
   from opening another doctor's patient, or a nurse touching a task that isn't
   theirs.

The two decorators are independent. Neither imports or calls the other, and they
can be stacked in either order on the same route:

    @app.get("/care-tasks/<int:task_id>")
    @require_role("nurse")
    @require_assignment(lambda task_id: get_care_task(task_id)["assigned_nurse_id"])
    def get_care_task_route(task_id):
        ...

RESPONSES
---------
Failures raise AuthError, carrying an HTTP status (401 / 403) and a message.
Register one error handler in the app so every route reports failures the same
way - see _example_error_handler() at the bottom of this file.
"""

from functools import wraps


# ------------------------------------------------------------
# The roles this service is allowed to serve. Anything not in
# this set fails require_role() no matter what the route asks for.
# ------------------------------------------------------------
CLINICAL_ROLES = frozenset({"doctor", "nurse", "specialist"})


# ------------------------------------------------------------
# THE STAND-IN USER
# Edit this dict by hand to test as someone else, then restart.
# ------------------------------------------------------------
CURRENT_USER = {
    "id": 1,
    "name": "Dr Daniel Chen",
    "role": "doctor",
}

# Handy copies from student-2/database/seed_data.sql. Paste one over
# CURRENT_USER above to switch who is "logged in".
_SAMPLE_USERS = {
    "doctor":      {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"},
    "doctor_2":    {"id": 2, "name": "Dr Priya Nair",  "role": "doctor"},
    "specialist":  {"id": 3, "name": "Dr Emily Brown", "role": "specialist"},
    "nurse":       {"id": 7, "name": "James Wilson",   "role": "nurse"},
    "nurse_2":     {"id": 8, "name": "Aisha Khan",     "role": "nurse"},
    # For checking that non-clinical roles are always rejected:
    "outsider":    {"id": 99, "name": "Pat Adams",     "role": "receptionist"},
}


class AuthError(Exception):
    """Raised when a request fails a role or assignment check.

    status is the HTTP code the app should return (401 = not a usable
    identity at all, 403 = known clinical user but not allowed here).
    """

    def __init__(self, message, status=403):
        super().__init__(message)
        self.message = message
        self.status = status


# ------------------------------------------------------------
# Current-user helpers
# The rest of the service should call get_current_user() rather than
# touching CURRENT_USER directly - that's the one line that changes
# when real auth lands.
# ------------------------------------------------------------
def get_current_user():
    """Return the acting user dict. Later: derived from the shared auth layer."""
    return CURRENT_USER


def _current_role():
    user = get_current_user() or {}
    return user.get("role")


def _current_user_id():
    user = get_current_user() or {}
    return user.get("id")


# ------------------------------------------------------------
# Decorator 1: role check
# ------------------------------------------------------------
def require_role(*allowed_roles):
    """Allow the request through only if the current user's role is acceptable.

    Pass one or more of "doctor" / "nurse" / "specialist" to restrict to those.
    Pass nothing to mean "any clinical role in this service".

    A role outside CLINICAL_ROLES (or a missing / unknown role) is always
    rejected, even if it is somehow passed in allowed_roles - this service
    never serves non-clinical roles.
    """
    # Narrow whatever was requested down to roles this service is allowed to
    # serve. If the caller passed nothing, that's every clinical role.
    if allowed_roles:
        permitted = CLINICAL_ROLES.intersection(allowed_roles)
    else:
        permitted = set(CLINICAL_ROLES)

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            role = _current_role()

            if role not in CLINICAL_ROLES:
                raise AuthError(
                    "This service is restricted to clinical staff "
                    "(doctor, nurse, specialist).",
                    status=403,
                )

            if role not in permitted:
                raise AuthError(
                    "Your role ({}) is not permitted on this endpoint.".format(role),
                    status=403,
                )

            return view(*args, **kwargs)

        return wrapper

    return decorator


# ------------------------------------------------------------
# Decorator 2: assignment check
# ------------------------------------------------------------
def require_assignment(lookup):
    """Allow the request through only if the current user is assigned to the record.

    `lookup` is a callable you supply. It receives the same positional and
    keyword arguments the view receives (typically the URL params, e.g.
    task_id), and must return the staff id assigned to that record - for
    example care_tasks.assigned_nurse_id, clinical_records.doctor_id, or
    consultation_requests.specialist_id / .requesting_doctor_id.

    The decorator compares that id to the current user's id. Records that the
    lookup can't find (returns None) are treated as not accessible.

    This does not look at roles at all - stack it with require_role() when you
    need both. The two do not depend on each other.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            assigned_staff_id = lookup(*args, **kwargs)

            if assigned_staff_id is None:
                raise AuthError(
                    "Record not found, or it has no assigned staff member.",
                    status=404,
                )

            if assigned_staff_id != _current_user_id():
                raise AuthError(
                    "You are not assigned to this record.",
                    status=403,
                )

            return view(*args, **kwargs)

        return wrapper

    return decorator


# ------------------------------------------------------------
# Wiring example (keep, don't call from here)
# ------------------------------------------------------------
def _example_error_handler():
    """How to surface AuthError uniformly. Register this in app.py:

        from auth import AuthError

        @app.errorhandler(AuthError)
        def handle_auth_error(err):
            return {"error": err.message}, err.status
    """
    raise NotImplementedError("Reference only - copy into app.py.")
