"""Post-commit CI/CD endpoint tests for the Room & Bed backend/API (Release 2).

These run in student-4.yml against the real containers after a push, not
against the in-process test client. Two endpoint functions are tested:

1. ``create_bed``  - POST /api/beds
2. ``decide_case`` - PUT  /api/shortage-cases/<id>/decide

They are skipped unless STUDENT4_API_URL is set (for example
http://localhost:5400), so the ordinary unit test run is unaffected.
"""

import json
import os
import urllib.error
import urllib.request
import uuid

import pytest

API_URL = os.environ.get("STUDENT4_API_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not API_URL.startswith(("http://", "https://")),
    reason="set STUDENT4_API_URL to run the live endpoint tests",
)


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310 - scheme checked by pytestmark
        API_URL + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def first(path):
    status, body = call("GET", path)
    assert status == 200 and body["success"], body
    assert body["data"], "seed data missing for " + path
    return body["data"][0]


# --------------------------------------------------------------------------
# Endpoint function 1: create_bed (POST /api/beds)
# --------------------------------------------------------------------------


def test_create_bed_stores_a_new_available_bed():
    room = first("/api/rooms")
    bed_number = "CI-" + uuid.uuid4().hex[:6].upper()

    status, body = call("POST", "/api/beds", {"room_id": room["room_id"], "bed_number": bed_number})

    assert status == 201 and body["success"], body
    created = body["data"]
    assert created["bed_number"] == bed_number and created["room_id"] == room["room_id"]
    status, stored = call("GET", "/api/beds/{}".format(created["bed_id"]))
    assert status == 200 and stored["data"]["bed_number"] == bed_number


def test_create_bed_rejects_missing_fields_and_bad_status():
    status, body = call("POST", "/api/beds", {"bed_number": "CI-NO-ROOM"})
    assert status == 400 and body["success"] is False and "room_id" in body["error"]

    room = first("/api/rooms")
    status, body = call("POST", "/api/beds", {"room_id": room["room_id"], "bed_number": "CI-BAD",
                                              "status": "broken"})
    assert status == 400 and body["success"] is False


# --------------------------------------------------------------------------
# Endpoint function 2: decide_case (PUT /api/shortage-cases/<id>/decide)
# --------------------------------------------------------------------------


def open_new_case():
    status, body = call("POST", "/api/shortage-cases", {
        "patient_id": 9000 + uuid.uuid4().int % 999, "required_care_category": "Long-term",
        "urgency": "High", "holding_location": "CI corridor"})
    assert status == 201, body
    return body["data"]


def test_decide_case_reserves_the_chosen_bed():
    case = open_new_case()
    status, body = call("GET", "/api/shortage-cases/{}/options".format(case["case_id"]))
    beds = [o for o in body["data"]["options"] if o["bed_id"] and o["kind"] != "pending_cleaning"]
    if not beds:
        pytest.skip("no free Long-term bed in this database")
    option = beds[0]

    status, body = call("PUT", "/api/shortage-cases/{}/decide".format(case["case_id"]), {
        "chosen_option": option["note"], "decision_reason": "CI endpoint test",
        "resolved_bed_id": option["bed_id"]})

    assert status == 200 and body["success"], body
    assert body["data"]["status"] == "Option offered"
    assert body["data"]["resolved_bed_id"] == option["bed_id"]
    assert call("GET", "/api/beds/{}".format(option["bed_id"]))[1]["data"]["status"] == "reserved"


def test_decide_case_requires_a_reason_and_refuses_closed_cases():
    case = open_new_case()
    status, body = call("PUT", "/api/shortage-cases/{}/decide".format(case["case_id"]),
                        {"chosen_option": "Escalate"})
    assert status == 400 and "decision_reason" in body["error"]

    status, _ = call("PUT", "/api/shortage-cases/{}/cancel".format(case["case_id"]),
                     {"decision_reason": "CI clean-up"})
    assert status == 200
    status, body = call("PUT", "/api/shortage-cases/{}/decide".format(case["case_id"]),
                        {"chosen_option": "Escalate", "decision_reason": "late", "escalate": True})
    assert status == 409 and body["success"] is False
