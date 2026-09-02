"""HTTP client used by the Student 3 frontend to call its backend API."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

API_BASE_URL = os.environ.get("BACKEND_API_URL", "http://localhost:5300").rstrip("/")


class BackendError(RuntimeError):
    """The Student 3 backend could not return a usable response."""
    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def _request(path: str, method: str = "GET", payload: dict | None = None,
             role: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if role:
        headers["X-HOMS-Role"] = role
    call = urllib.request.Request(f"{API_BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(call, timeout=5) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try: message = json.load(exc).get("error", "Backend request failed")
        except (json.JSONDecodeError, AttributeError): message = "Backend request failed"
        raise BackendError(message, exc.code) from exc
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise BackendError("Backend service unavailable") from exc


def list_staff() -> list[dict]:
    """Return staff records from the backend API, never from SQLite directly."""
    payload = _request("/api/staff")

    staff = payload.get("staff")
    if not isinstance(staff, list):
        raise BackendError("Backend response did not contain a staff list")
    return staff


def get_staff(staff_id: int) -> dict:
    """Return one staff record from the backend API."""
    payload = _request(f"/api/staff/{staff_id}")

    staff = payload.get("staff")
    if not isinstance(staff, dict):
        raise BackendError("Backend response did not contain a staff record")
    return staff


def list_suppliers(status="active", search=""):
    payload = _request("/api/suppliers?" + urllib.parse.urlencode({"status": status, "search": search}))
    return payload["suppliers"]


def get_supplier(supplier_id):
    return _request(f"/api/suppliers/{supplier_id}")


def save_supplier(payload, role, supplier_id=None):
    path = f"/api/suppliers/{supplier_id}" if supplier_id else "/api/suppliers"
    return _request(path, "PUT" if supplier_id else "POST", payload, role)


def deactivate_supplier(supplier_id, role):
    return _request(f"/api/suppliers/{supplier_id}", "DELETE", role=role)


def reactivate_supplier(supplier_id, role):
    return _request(f"/api/suppliers/{supplier_id}/reactivate", "POST", {}, role)


def list_medicines(**filters):
    payload = _request("/api/medicines?" + urllib.parse.urlencode(filters))
    return payload


def get_medicine(medicine_id):
    return _request(f"/api/medicines/{medicine_id}")


def save_medicine(payload, role, medicine_id=None):
    path = f"/api/medicines/{medicine_id}" if medicine_id else "/api/medicines"
    return _request(path, "PUT" if medicine_id else "POST", payload, role)


def deactivate_medicine(medicine_id, role):
    return _request(f"/api/medicines/{medicine_id}", "DELETE", role=role)


def reactivate_medicine(medicine_id, role):
    return _request(f"/api/medicines/{medicine_id}/reactivate", "POST", {}, role)


def list_stock_movements(**filters):
    return _request("/api/stock/movements?" + urllib.parse.urlencode(filters))

def list_batches(**filters): return _request("/api/batches?" + urllib.parse.urlencode(filters))
def write_off_batch(batch_id, reason, role): return _request(f"/api/batches/{batch_id}/write-off", "POST", {"reason":reason}, role)
def issue_stock(payload, role): return _request("/api/stock/issue", "POST", payload, role)
def receive_stock(payload, role): return _request("/api/stock/receive", "POST", payload, role)
