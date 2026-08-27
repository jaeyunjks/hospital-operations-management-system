"""Client for the shared Authentication & RBAC service (port 5000).

The HOMS architecture requires every feature service to validate
identity and permissions through the shared service rather than
implementing its own login. That service is owned by the team, not by
this feature, so while AUTH_ENABLED is off this module returns a
development identity and the rest of the code is unchanged when it is
switched on.

Room & Bed permissions (Architecture v2.2, Table 6):
    Receptionist / bed coordinator  bed.allocate, bed.release
    System Administrator            room.configure
    Doctor / Nurse / Specialist     read only
"""

import requests
from flask import request

import config
from responses import ApiError

DEV_IDENTITY = {"username": "dev.coordinator", "role": "Receptionist",
                "permissions": ["bed.allocate", "bed.release", "room.configure"]}


def current_user():
    """Return the caller's identity, validating the token when enabled."""
    if not config.AUTH_ENABLED:
        return DEV_IDENTITY

    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise ApiError("Missing Authorization header", status=401)

    try:
        response = requests.get(
            config.AUTH_URL + "/api/validate",
            headers={"Authorization": "Bearer " + token},
            timeout=5,
        )
    except requests.RequestException as error:
        raise ApiError("Authentication service unavailable: {}".format(error), status=503) from error

    if response.status_code != 200:
        raise ApiError("Invalid or expired session", status=401)
    return response.json().get("data", {})


def require_permission(permission):
    """Return the caller once their role grants `permission`."""
    user = current_user()
    if permission not in user.get("permissions", []):
        raise ApiError(
            "Role '{}' is not permitted to {}".format(user.get("role"), permission),
            status=403,
        )
    return user
