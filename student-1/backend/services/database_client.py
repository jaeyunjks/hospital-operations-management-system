# Client for the Patient & Admissions Management System database microservice.
# Creation date: 31/08/2026

from __future__ import annotations

import requests

try:
    from backend.config import DATABASE_TIMEOUT, DATABASE_URL
    from backend.responses import ApiError
except ImportError:  # pragma: no cover - supports direct module execution
    import config
    from responses import ApiError

    DATABASE_URL = config.DATABASE_URL
    DATABASE_TIMEOUT = config.DATABASE_TIMEOUT

TIMEOUT = DATABASE_TIMEOUT


def _call(method, path, payload=None, params=None):
    url = f"{DATABASE_URL.rstrip('/')}{path}"
    try:
        response = requests.request(
            method=method,
            url=url,
            json=payload,
            params=params,
            timeout=TIMEOUT,
        )
        try:
            data = response.json()
        except ValueError:
            data = {"message": response.text}

        if not response.ok:
            raise ApiError(
                data.get("error") or data.get("message") or "Unknown error",
                status=response.status_code,
            )

        return data
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"Database service unavailable: {exc}", status=503) from exc


def _normalize_params(include_inactive=False, **filters):
    params = {key: value for key, value in filters.items() if value not in (None, "")}
    if include_inactive:
        params["includeInactive"] = "true"
    return params or None


# Generic CRUD operations for the database microservice
# -----------------------------------------------------------------------

# Return all records for a given resource, optionally including soft-deleted ones.
def list_records(resource, include_inactive=False, **filters):
    params = _normalize_params(include_inactive=include_inactive, **filters)
    return _call("GET", f"/{resource}", params=params)

# Fetch one record by ID.
def get_record(resource, record_id, include_inactive=False):
    params = _normalize_params(include_inactive=include_inactive)
    return _call("GET", f"/{resource}/{record_id}", params=params)

# Create a new record for a given resource.
def create_record(resource, payload):
    return _call("POST", f"/{resource}", payload=payload or {})

# Update a record by ID.
def update_record(resource, record_id, payload):
    return _call("PATCH", f"/{resource}/{record_id}", payload=payload or {})

# Soft delete a record when supported by the resource.
def delete_record(resource, record_id):
    return _call("DELETE", f"/{resource}/{record_id}")

# Restore a soft-deleted record when supported by the resource.
def restore_record(resource, record_id):
    return _call("POST", f"/{resource}/{record_id}/restore")


# Convenience wrappers for Student-1 domain resources

def list_patients(include_inactive=False, **filters):
    return list_records("patients", include_inactive=include_inactive, **filters)

def get_patient(patient_id, include_inactive=False):
    return get_record("patients", patient_id, include_inactive=include_inactive)

def create_patient(payload):
    return create_record("patients", payload)

def update_patient(patient_id, payload):
    return update_record("patients", patient_id, payload)

def delete_patient(patient_id):
    return delete_record("patients", patient_id)

def restore_patient(patient_id):
    return restore_record("patients", patient_id)

def list_admissions(include_inactive=False, **filters):
    return list_records("admissions", include_inactive=include_inactive, **filters)

def get_admission(admission_id, include_inactive=False):
    return get_record("admissions", admission_id, include_inactive=include_inactive)

def create_admission(payload):
    return create_record("admissions", payload)

def update_admission(admission_id, payload):
    return update_record("admissions", admission_id, payload)

# Admissions do not currently support deletion or restoration, 
# so these functions are commented out until the database service supports them.

# def delete_admission(admission_id):
#     return delete_record("admissions", admission_id)

# def restore_admission(admission_id):
#     return restore_record("admissions", admission_id)

__all__ = [
    "_call",
    "list_records",
    "get_record",
    "create_record",
    "update_record",
    "delete_record",
    "restore_record",
    "list_patients",
    "get_patient",
    "create_patient",
    "update_patient",
    "delete_patient",
    "restore_patient",
    "list_admissions",
    "get_admission",
    "create_admission",
    "update_admission",
    # "delete_admission",
    # "restore_admission",
]

