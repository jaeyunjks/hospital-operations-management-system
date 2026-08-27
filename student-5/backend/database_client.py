"""HTTP client for the Student 5 database microservice.

This module is the backend's only route to its data. The backend never opens
the SQLite file, imports the database package, or shares a process with it —
all access crosses an HTTP boundary:

    Backend/API Microservice --HTTP--> Database Microservice --> SQLite

Implemented with the standard library ``urllib`` so the backend adds no
dependency beyond Flask.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from config import Config
from errors import ConflictError, DatabaseServiceError, NotFoundError


class DatabaseClient:
    """Thin REST client over the database microservice."""

    def __init__(self, base_url: Optional[str] = None, timeout: Optional[float] = None):
        self.base_url = (base_url or Config.DATABASE_SERVICE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else Config.DATABASE_SERVICE_TIMEOUT

    # ------------------------------------------------------------- internals
    def _request(self, method: str, path: str,
                 params: Optional[Dict[str, Any]] = None,
                 payload: Optional[Dict[str, Any]] = None) -> Any:
        """Perform one HTTP call and translate transport errors into ApiErrors."""
        url = f"{self.base_url}{path}"

        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url = f"{url}?{urllib.parse.urlencode(filtered)}"

        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status == 204:
                    return None
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None

        except urllib.error.HTTPError as error:
            detail = self._decode_error(error)
            if error.code == 404:
                raise NotFoundError(detail) from error
            if error.code == 409:
                raise ConflictError(detail) from error
            raise DatabaseServiceError(
                f"Database service returned {error.code}: {detail}") from error

        except urllib.error.URLError as error:
            raise DatabaseServiceError(
                f"Database service unreachable at {self.base_url}: {error.reason}") from error

        except json.JSONDecodeError as error:
            raise DatabaseServiceError("Database service returned malformed JSON.") from error

    @staticmethod
    def _decode_error(error: urllib.error.HTTPError) -> str:
        try:
            return json.loads(error.read().decode("utf-8")).get("message", error.reason)
        except Exception:
            return str(error.reason)

    # ---------------------------------------------------------------- health
    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    # ----------------------------------------------------------------- staff
    def list_staff(self, department: Optional[str] = None, role: Optional[str] = None,
                   availability_status: Optional[str] = None,
                   employment_status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._request("GET", "/staff", params={
            "department": department,
            "role": role,
            "availability_status": availability_status,
            "employment_status": employment_status,
        })

    def get_staff(self, staff_id: int) -> Dict[str, Any]:
        return self._request("GET", f"/staff/{staff_id}")

    def update_staff(self, staff_id: int, **fields: Any) -> Dict[str, Any]:
        return self._request("PATCH", f"/staff/{staff_id}", payload=fields)

    def list_weekly_availability(self, staff_id: int) -> List[Dict[str, Any]]:
        return self._request("GET", f"/staff/{staff_id}/weekly-availability")

    def replace_weekly_availability(self, staff_id: int,
                                    periods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return self._request("PUT", f"/staff/{staff_id}/weekly-availability",
                              payload={"periods": periods})

    # ------------------------------------------ unavailability requests
    def list_unavailability_requests(self, staff_id: Optional[int] = None,
                                     request_status: Optional[str] = None
                                     ) -> List[Dict[str, Any]]:
        return self._request("GET", "/unavailability-requests", params={
            "staff_id": staff_id, "request_status": request_status})

    def get_unavailability_request(self, request_id: int) -> Dict[str, Any]:
        return self._request("GET", f"/unavailability-requests/{request_id}")

    def list_overlapping_requests(self, staff_id: int, start_date: str,
                                  end_date: str,
                                  exclude_request_id: Optional[int] = None
                                  ) -> List[Dict[str, Any]]:
        return self._request("GET", "/unavailability-requests/overlapping", params={
            "staff_id": staff_id, "start_date": start_date, "end_date": end_date,
            "exclude_request_id": exclude_request_id})

    def create_unavailability_request(self, **fields: Any) -> Dict[str, Any]:
        return self._request("POST", "/unavailability-requests", payload=fields)

    def update_unavailability_request(self, request_id: int,
                                      **fields: Any) -> Dict[str, Any]:
        return self._request("PATCH", f"/unavailability-requests/{request_id}",
                              payload=fields)

    def list_shifts_for_staff(self, staff_id: int) -> List[Dict[str, Any]]:
        return self._request("GET", f"/staff/{staff_id}/shifts")

    # ---------------------------------------------------------------- shifts
    def list_shifts(self, department: Optional[str] = None,
                    shift_date: Optional[str] = None,
                    shift_status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._request("GET", "/shifts", params={
            "department": department,
            "shift_date": shift_date,
            "shift_status": shift_status,
        })

    def get_shift(self, shift_id: int) -> Dict[str, Any]:
        return self._request("GET", f"/shifts/{shift_id}")

    def create_shift(self, **fields: Any) -> Dict[str, Any]:
        return self._request("POST", "/shifts", payload=fields)

    def update_shift(self, shift_id: int, **fields: Any) -> Dict[str, Any]:
        return self._request("PATCH", f"/shifts/{shift_id}", payload=fields)

    def delete_shift(self, shift_id: int) -> None:
        self._request("DELETE", f"/shifts/{shift_id}")

    def list_staff_for_shift(self, shift_id: int) -> List[Dict[str, Any]]:
        return self._request("GET", f"/shifts/{shift_id}/staff")

    # ----------------------------------------------------------- assignments
    def list_assignments(self, shift_id: Optional[int] = None,
                         staff_id: Optional[int] = None,
                         assignment_status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._request("GET", "/assignments", params={
            "shift_id": shift_id,
            "staff_id": staff_id,
            "assignment_status": assignment_status,
        })

    def create_assignment(self, **fields: Any) -> Dict[str, Any]:
        return self._request("POST", "/assignments", payload=fields)

    def update_assignment(self, assignment_id: int, **fields: Any) -> Dict[str, Any]:
        return self._request("PATCH", f"/assignments/{assignment_id}", payload=fields)

    def delete_assignment(self, assignment_id: int) -> None:
        self._request("DELETE", f"/assignments/{assignment_id}")


#: Shared client instance used by the service layer.
database_client = DatabaseClient()
