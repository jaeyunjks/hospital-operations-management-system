"""Client for this feature's own database microservice (port 6400).

The backend never opens rooms.db. Every read and write goes through
this HTTP client, which keeps the service boundary honest and lets the
database container be replaced or moved without touching route code.
"""

import requests

import config
from responses import ApiError

TIMEOUT = config.DATABASE_TIMEOUT


def _call(method, path, params=None, json=None):
    url = config.DATABASE_URL + path
    try:
        response = requests.request(method, url, params=params, json=json, timeout=TIMEOUT)
    except requests.RequestException as error:
        raise ApiError(
            "Database service unavailable at {}: {}".format(config.DATABASE_URL, error),
            status=503,
        ) from error

    try:
        body = response.json()
    except ValueError:
        raise ApiError("Database service returned a non-JSON response", status=502) from None

    if not body.get("success"):
        raise ApiError(body.get("error") or "Database request failed", status=response.status_code)
    return body["data"]


# --- generic CRUD -----------------------------------------------------
def list_records(resource, **filters):
    clean = {k: v for k, v in filters.items() if v not in (None, "")}
    return _call("GET", "/db/" + resource, params=clean)


def get_record(resource, record_id):
    return _call("GET", "/db/{}/{}".format(resource, record_id))


def create_record(resource, payload):
    return _call("POST", "/db/" + resource, json=payload)


def update_record(resource, record_id, payload):
    return _call("PUT", "/db/{}/{}".format(resource, record_id), json=payload)


def delete_record(resource, record_id):
    return _call("DELETE", "/db/{}/{}".format(resource, record_id))


# --- views ------------------------------------------------------------
def availability(care_category=None, bed_status=None, ward=None):
    return _call("GET", "/db/views/availability", params={
        k: v for k, v in {
            "care_category": care_category, "bed_status": bed_status, "ward": ward
        }.items() if v
    })


def theatre_board():
    return _call("GET", "/db/views/theatre-board")


def ward_occupancy(ward=None):
    return _call("GET", "/db/views/ward-occupancy",
                 params={"ward": ward} if ward else None)


def occupancy_stats():
    return _call("GET", "/db/views/occupancy-stats")


def overlapping(bed_id, start, end=None, exclude_id=None):
    return _call("GET", "/db/arrangements/overlaps", params={
        k: v for k, v in {
            "bed_id": bed_id, "start": start, "end": end, "exclude_id": exclude_id
        }.items() if v
    })


def health():
    return _call("GET", "/health")
