# Role-based authorization for the Student 1 backend/API microservice.

# Release 0 security limitation:
# - The caller's identity is simulated from request headers, not verified tokens.
# - This is intentionally simple and mirrors the same design used in the other
#   student services for local development and demonstration.
# - The permission model is enforced server-side, even though the request role is
#   not authenticated cryptographically.


from __future__ import annotations

from typing import Any, Dict

from flask import request

ROLE_MANAGER = "System Admin"
ROLE_RECEPTIONIST = "Receptionist"
ROLE_DOCTOR = "Doctor"
ROLE_NURSE = "Nurse"
ROLE_SPECIALIST = "Specialist"

ROLES = (ROLE_MANAGER, ROLE_RECEPTIONIST, ROLE_DOCTOR, ROLE_NURSE, ROLE_SPECIALIST)

ROLE_HEADER = "X-HOMS-Role"
USER_ID_HEADER = "X-HOMS-User-Id"

# Raised when a caller does not have the required role or permission.
class AuthError(Exception):

    def __init__(self, message: str, status_code: int = 403, details: Dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}

# Return the simulated caller identity from request headers.
# This is the only function that would need to change when the shared HOMS
# authentication service is added: it would read a verified token instead of
# trusting headers.
def identity_from_request() -> Dict[str, Any]:
    role = request.headers.get(ROLE_HEADER) or ROLE_MANAGER
    if role not in ROLES:
        role = ROLE_MANAGER

    raw_user_id = request.headers.get(USER_ID_HEADER)
    try:
        user_id = int(raw_user_id) if raw_user_id else None
    except (TypeError, ValueError):
        user_id = None

    return {"role": role, "user_id": user_id}

# Allow only callers whose role is in the supplied list. 
def require_role(*allowed_roles: str) -> Dict[str, Any]:
    identity = identity_from_request()
    if identity["role"] not in allowed_roles:
        raise AuthError(
            "This operation requires one of the allowed roles.",
            status_code=403,
            details={"role": identity["role"], "allowed_roles": list(allowed_roles)},
        )
    return identity

# System-wide administration: patient or admission maintenance tasks.
def require_manager() -> Dict[str, Any]:
    return require_role(ROLE_MANAGER)

# Read patient records: all clinical roles may view patient data.
def require_patient_read() -> Dict[str, Any]:
    return require_role(ROLE_MANAGER, ROLE_RECEPTIONIST, ROLE_DOCTOR, ROLE_NURSE, ROLE_SPECIALIST)

# Create or update patient records: manager and reception staff.
def require_patient_write() -> Dict[str, Any]:
    return require_role(ROLE_MANAGER, ROLE_RECEPTIONIST)

# Delete / deactivate patient records: system administrator only.
def require_patient_delete() -> Dict[str, Any]:
    return require_role(ROLE_MANAGER)

# Read admission records: manager, reception, medical, and specialist staff.
def require_admission_read() -> Dict[str, Any]:
    return require_role(ROLE_MANAGER, ROLE_RECEPTIONIST, ROLE_DOCTOR, ROLE_NURSE, ROLE_SPECIALIST)

# Create or update admission records: manager, reception, and doctor staff.
def require_admission_write() -> Dict[str, Any]:
    return require_role(ROLE_MANAGER, ROLE_RECEPTIONIST, ROLE_DOCTOR)

# Administrative admission removal: manager only
def require_admission_delete() -> Dict[str, Any]:
    return require_role(ROLE_MANAGER)

# Permit either a matching role or the matching user identity.
def require_role_or_self(role: str, user_id: int | None) -> Dict[str, Any]:
    identity = identity_from_request()
    if identity["role"] == role:
        return identity
    if identity.get("user_id") == user_id:
        return identity
    raise AuthError(
        "You are not allowed to access this record.",
        status_code=403,
        details={"role": identity["role"], "requested_user_id": user_id},
    )

__all__ = [
    "AuthError",
    "ROLE_MANAGER",
    "ROLE_RECEPTIONIST",
    "ROLE_DOCTOR",
    "ROLE_NURSE",
    "ROLE_SPECIALIST",
    "ROLES",
    "ROLE_HEADER",
    "USER_ID_HEADER",
    "identity_from_request",
    "require_role",
    "require_manager",
    "require_patient_read",
    "require_patient_write",
    "require_patient_delete",
    "require_admission_read",
    "require_admission_write",
    "require_admission_delete",
    "require_role_or_self",
]