"""Post-commit CI/CD endpoint tests for the Clinical Staff Management backend (Release 2).

These run in student-2.yml against the real containers after a push, not
against the in-process test client. Two endpoint functions are tested:

1. ``acknowledge_task`` - PUT  /api/care-tasks/<id>/acknowledge
2. ``create_record``    - POST /api/clinical-records/

They are skipped unless STUDENT2_BACKEND_URL is set (for example
http://localhost:5200), so the ordinary unit test run is unaffected.

Every test creates the rows it needs and never edits a seed row, so the file
can run twice against the same database. The caller is chosen with the
X-User-Role header: doctor is Dr Daniel Chen (id 1), nurse is James Wilson
(id 7).

Creating a clinical record is gated on the admission being active. In CI
student-2.yml turns on the local stub (STUDENT2_USE_EXTERNAL_STUBS=true), which
treats every admission id as active. Against the full Docker stack the real
Student 1 service answers instead, so ADMISSION_ID must be one that is Active in
Student 1's seed: admission 2 is, and Student 2's seed also lists it as active.
"""

import json
import os
import urllib.error
import urllib.request
import uuid

import pytest

BACKEND_URL = os.environ.get("STUDENT2_BACKEND_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not BACKEND_URL.startswith(("http://", "https://")),
    reason="set STUDENT2_BACKEND_URL to run the live endpoint tests",
)

DOCTOR_ID = 1  # Dr Daniel Chen, the "doctor" role
NURSE_ID = 7  # James Wilson, the "nurse" role
PATIENT_ID = 2  # seed patient Thomas Reed ...
ADMISSION_ID = 2  # ... on his admission, Active in both Student 1's and Student 2's seed (reference values only)


def call(method, path, payload=None, role="doctor"):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"X-User-Role": role}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(  # noqa: S310 - scheme checked by pytestmark
        BACKEND_URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())


def _decode(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return {"error": raw.decode(errors="replace")}


def marker():
    return "CI-" + uuid.uuid4().hex[:8].upper()


def record_payload(**overrides):
    payload = {"patient_id": PATIENT_ID, "admission_id": ADMISSION_ID, "doctor_id": DOCTOR_ID,
               "assessment_notes": "CI endpoint test " + marker()}
    payload.update(overrides)
    return payload


def new_record():
    status, body = call("POST", "/api/clinical-records/", record_payload())
    assert status == 201, body
    return body["clinical_record"]


def new_task(record_id=None):
    record_id = record_id or new_record()["record_id"]
    status, body = call("POST", "/api/care-tasks/", {
        "clinical_record_id": record_id, "assigned_nurse_id": NURSE_ID,
        "task_description": "CI endpoint test " + marker()})
    assert status == 201, body
    return body["care_task"]


# --------------------------------------------------------------------------
# Endpoint function 1: acknowledge_task (PUT /api/care-tasks/<id>/acknowledge)
# --------------------------------------------------------------------------


def test_acknowledge_moves_a_pending_task_to_acknowledged():
    task = new_task()
    assert task["status"] == "pending" and task["assigned_nurse_id"] == NURSE_ID
    path = "/api/care-tasks/{}".format(task["task_id"])

    status, body = call("PUT", path + "/acknowledge", role="nurse")

    assert status == 200, body
    assert body["care_task"]["status"] == "acknowledged"
    status, stored = call("GET", path, role="nurse")
    assert status == 200 and stored["care_task"]["status"] == "acknowledged"


def test_acknowledging_again_is_a_no_op_not_a_second_transition():
    task = new_task()
    path = "/api/care-tasks/{}/acknowledge".format(task["task_id"])
    assert call("PUT", path, role="nurse")[0] == 200

    status, body = call("PUT", path, role="nurse")

    assert status == 200, body
    assert body["note"] == "already acknowledged"
    assert body["care_task"]["status"] == "acknowledged"


def test_acknowledge_is_refused_for_a_doctor_and_for_a_cancelled_task():
    task = new_task()
    path = "/api/care-tasks/{}".format(task["task_id"])

    status, body = call("PUT", path + "/acknowledge", role="doctor")
    assert status == 403 and "error" in body
    assert call("GET", path, role="nurse")[1]["care_task"]["status"] == "pending"

    status, _ = call("DELETE", path, role="doctor")
    assert status == 200
    status, body = call("PUT", path + "/acknowledge", role="nurse")
    assert status == 409 and "cannot acknowledge" in body["error"]
    assert call("GET", path, role="nurse")[1]["care_task"]["status"] == "cancelled"


# --------------------------------------------------------------------------
# Endpoint function 2: create_record (POST /api/clinical-records/)
# --------------------------------------------------------------------------


def test_create_record_stores_the_note_and_can_be_read_back():
    notes = "CI marker " + marker()

    status, body = call("POST", "/api/clinical-records/", record_payload(assessment_notes=notes))

    assert status == 201, body
    created = body["clinical_record"]
    assert created["assessment_notes"] == notes
    assert created["doctor_id"] == DOCTOR_ID
    assert created["patient_id"] == PATIENT_ID and created["admission_id"] == ADMISSION_ID
    status, stored = call("GET", "/api/clinical-records/{}".format(created["record_id"]))
    assert status == 200
    assert stored["clinical_record"]["assessment_notes"] == notes
    assert stored["clinical_record"]["record_id"] == created["record_id"]


@pytest.mark.parametrize("field", ["patient_id", "admission_id", "doctor_id", "assessment_notes"])
def test_create_record_rejects_a_missing_required_field(field):
    payload = record_payload()
    del payload[field]

    status, body = call("POST", "/api/clinical-records/", payload)

    assert status == 400 and field in body["error"]
