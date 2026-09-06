"""
database_client.py - the only way backend route files talk to the database API.

The database container (student-2/database/app.py) runs on port 6200 and
exposes plain REST CRUD over five tables:
  clinical_records, consultation_requests, care_tasks,
  surgery_requests, ai_summaries

Every table has the same five endpoints:
  GET    /<table>            -> list all rows
  POST   /<table>            -> create a row (JSON body), returns the new row
  GET    /<table>/<id>       -> fetch one row
  PUT    /<table>/<id>       -> partial update (JSON body), returns the row
  DELETE /<table>/<id>       -> delete one row

This module wraps those calls with the `requests` library and gives each
operation its own named function. Route files import from here instead of
building URLs or calling requests directly.

Error handling contract:
  * If the database API cannot be reached, or replies with a non-2xx status,
    a DatabaseClientError is raised. Nothing fails silently.
  * Route files are expected to catch DatabaseClientError and turn it into
    whatever HTTP response they want to send their own callers.

Deliberately NOT included: retry logic, caching, connection pooling.
Keep this layer thin.
"""

import os

import requests

# Base URL of the database API. Overridable via env var for Docker/deployment;
# defaults to localhost for local development.
BASE_URL = os.environ.get("DATABASE_API_URL", "http://localhost:6200").rstrip("/")

# Per-request timeout (seconds). Connect + read. Not configurable on purpose.
TIMEOUT = 10


class DatabaseClientError(Exception):
    """
    Raised for any problem talking to the database API:
      * the API is unreachable / timed out / refused the connection
      * the API returned a non-2xx status code

    `status_code` is the HTTP status when there was a response, else None.
    `response_body` is the raw text of the error response, if any.
    """

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


# ------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------
def _request(method, path, json_body=None):
    """
    Make one HTTP call to the database API and return the parsed JSON body.

    Raises DatabaseClientError if the API is unreachable or answers non-2xx.
    """
    url = f"{BASE_URL}{path}"

    try:
        response = requests.request(method, url, json=json_body, timeout=TIMEOUT)
    except requests.RequestException as exc:
        # DNS failure, connection refused, timeout, etc. - no HTTP response.
        raise DatabaseClientError(
            f"could not reach database API at {url}: {exc}"
        ) from exc

    # Any non-2xx is an error the caller must handle.
    if not response.ok:
        raise DatabaseClientError(
            f"database API returned {response.status_code} for {method} {path}",
            status_code=response.status_code,
            response_body=response.text,
        )

    # DELETE and others still return JSON in this API, but guard anyway.
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise DatabaseClientError(
            f"database API returned non-JSON body for {method} {path}: {response.text}"
        ) from exc


def _list(table):
    """GET /<table> - return a list of all rows."""
    return _request("GET", f"/{table}")


def _get(table, row_id):
    """GET /<table>/<id> - return one row."""
    return _request("GET", f"/{table}/{row_id}")


def _create(table, data):
    """POST /<table> - create a row from `data` (dict), return the new row."""
    return _request("POST", f"/{table}", json_body=data)


def _update(table, row_id, data):
    """PUT /<table>/<id> - partial update from `data` (dict), return the row."""
    return _request("PUT", f"/{table}/{row_id}", json_body=data)


def _delete(table, row_id):
    """DELETE /<table>/<id> - delete one row."""
    return _request("DELETE", f"/{table}/{row_id}")


# ============================================================
# clinical_records
# ============================================================
def list_clinical_records():
    return _list("clinical_records")


def get_clinical_record(record_id):
    return _get("clinical_records", record_id)


def create_clinical_record(data):
    return _create("clinical_records", data)


def update_clinical_record(record_id, data):
    return _update("clinical_records", record_id, data)


def delete_clinical_record(record_id):
    return _delete("clinical_records", record_id)


# ============================================================
# consultation_requests
# ============================================================
def list_consultation_requests():
    return _list("consultation_requests")


def get_consultation_request(request_id):
    return _get("consultation_requests", request_id)


def create_consultation_request(data):
    return _create("consultation_requests", data)


def update_consultation_request(request_id, data):
    return _update("consultation_requests", request_id, data)


def delete_consultation_request(request_id):
    return _delete("consultation_requests", request_id)


# ============================================================
# care_tasks
# ============================================================
def list_care_tasks():
    return _list("care_tasks")


def get_care_task(task_id):
    return _get("care_tasks", task_id)


def create_care_task(data):
    return _create("care_tasks", data)


def update_care_task(task_id, data):
    return _update("care_tasks", task_id, data)


def delete_care_task(task_id):
    return _delete("care_tasks", task_id)


# ============================================================
# surgery_requests
# ============================================================
def list_surgery_requests():
    return _list("surgery_requests")


def get_surgery_request(request_id):
    return _get("surgery_requests", request_id)


def create_surgery_request(data):
    return _create("surgery_requests", data)


def update_surgery_request(request_id, data):
    return _update("surgery_requests", request_id, data)


def delete_surgery_request(request_id):
    return _delete("surgery_requests", request_id)


# ============================================================
# ai_summaries
# ============================================================
def list_ai_summaries():
    return _list("ai_summaries")


def get_ai_summary(summary_id):
    return _get("ai_summaries", summary_id)


def create_ai_summary(data):
    return _create("ai_summaries", data)


def update_ai_summary(summary_id, data):
    return _update("ai_summaries", summary_id, data)


def delete_ai_summary(summary_id):
    return _delete("ai_summaries", summary_id)
