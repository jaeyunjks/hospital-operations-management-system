"""HTTP client used by the Student 3 frontend to call its backend API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API_BASE_URL = os.environ.get("STUDENT3_BACKEND_API_URL", "http://127.0.0.1:5300").rstrip("/")


class BackendError(RuntimeError):
    """The Student 3 backend could not return a usable response."""


def list_staff() -> list[dict]:
    """Return staff records from the backend API, never from SQLite directly."""
    try:
        with urllib.request.urlopen(f"{API_BASE_URL}/api/staff", timeout=5) as response:
            payload = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise BackendError(str(exc)) from exc

    staff = payload.get("staff")
    if not isinstance(staff, list):
        raise BackendError("Backend response did not contain a staff list")
    return staff


def get_staff(staff_id: int) -> dict:
    """Return one staff record from the backend API."""
    try:
        with urllib.request.urlopen(f"{API_BASE_URL}/api/staff/{staff_id}", timeout=5) as response:
            payload = json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise BackendError(str(exc)) from exc

    staff = payload.get("staff")
    if not isinstance(staff, dict):
        raise BackendError("Backend response did not contain a staff record")
    return staff
