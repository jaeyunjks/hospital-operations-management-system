"""
test_clinical_records.py - behaviour tests for backend/routes/clinical_records.py

These tests exercise the clinical_records blueprint through a real Flask test
client, but every outbound HTTP dependency is mocked:

  * services.database_client   (the REST client for the database container on
                                port 6200) is replaced with a MagicMock, so no
                                socket is ever opened for row CRUD.
  * services.admission_validation.require_active_for_create is patched per-test
    to simulate the Patient & Admission service (port 6100) reporting the
    admission as active / inactive / unreachable.

auth.CURRENT_USER is pinned to the doctor who "owns" the fixture record
(doctor_id == 1) so the blueprint's per-record access checks pass and we are
testing the admission / archive / scoping logic rather than authorisation.

Scenarios covered (one test minimum per listed edge case):
  1. POST succeeds (201) when the admission is active.
  2. POST is blocked (409) when the admission is not active.
  3. PUT sets updated_after_discharge = 1 when the admission has since ended.
  4. PUT leaves updated_after_discharge unset/0 while the admission is active.
  5. DELETE soft-deletes: it PUTs status -> 'archived' and never calls
     db.delete_clinical_record (the row is kept, not removed).
  6. GET /admission/<id> returns only rows for that admission, even when the
     same patient has records under a different admission.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# --- Make the backend package importable ------------------------------------
# clinical_records.py does `from services import database_client as db` and
# `from auth import ...`, which only resolve if backend/ is on sys.path. The
# tests live in student-2/tests/, so backend/ is ../backend relative to here.
BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "backend")
)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import auth  # noqa: E402  (import after sys.path tweak)
from routes import clinical_records as cr_module  # noqa: E402


# The doctor from the seed data whose id we stamp onto every fixture record so
# the blueprint's _assert_can_read_record / _assert_can_write_record pass.
ASSIGNED_DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}


def _make_record(record_id=10, admission_id=100, patient_id=55, status="active"):
    """Build a clinical_records row shaped the way the database API returns one."""
    return {
        "record_id": record_id,
        "patient_id": patient_id,
        "admission_id": admission_id,
        "doctor_id": ASSIGNED_DOCTOR["id"],
        "assessment_notes": "initial assessment",
        "diagnosis_summary": None,
        "care_plan": None,
        "status": status,
        "updated_after_discharge": 0,
    }


@pytest.fixture
def db_mock(monkeypatch):
    """
    Replace the whole database_client module reference used inside the route
    file with a MagicMock. Every db.* call the blueprint makes then returns a
    mock we control, and no real request to port 6200 is attempted.

    DatabaseClientError must stay a real exception class because the route does
    `except db.DatabaseClientError`, so we copy the genuine class onto the mock.
    """
    mock = MagicMock(name="database_client")
    mock.DatabaseClientError = cr_module.db.DatabaseClientError

    # Sensible defaults so unrelated helper calls (list_care_tasks, etc.) don't
    # blow up; individual tests override the ones they care about.
    mock.list_care_tasks.return_value = []
    mock.list_consultation_requests.return_value = []
    mock.list_clinical_records.return_value = []

    monkeypatch.setattr(cr_module, "db", mock)
    return mock


@pytest.fixture
def admission_active(monkeypatch):
    """
    Patch require_active_for_create to a no-op: the admission is 'Active', so
    the guard returns without raising. Used by the POST-succeeds and
    PUT-stays-0 tests.
    """
    stub = MagicMock(name="require_active_for_create", return_value=None)
    monkeypatch.setattr(cr_module, "require_active_for_create", stub)
    return stub


@pytest.fixture
def admission_inactive(monkeypatch):
    """
    Patch require_active_for_create to raise AdmissionNotActiveError, i.e. the
    Patient & Admission service reports the admission as 'Completed'. Used by
    the POST-blocked (409) and PUT-flags-1 tests.
    """
    def _raise(admission_id):
        raise cr_module.AdmissionNotActiveError(admission_id, "Completed")

    stub = MagicMock(name="require_active_for_create", side_effect=_raise)
    monkeypatch.setattr(cr_module, "require_active_for_create", stub)
    return stub


@pytest.fixture
def client(monkeypatch, db_mock):
    """
    A Flask test client with ONLY the clinical_records blueprint mounted (so we
    don't drag in the other route modules or app.py's importlib wiring), the
    AuthError handler registered, and CURRENT_USER pinned to the assigned
    doctor. db_mock is requested here so the patch is active before any request.
    """
    from flask import Flask, jsonify

    monkeypatch.setattr(auth, "CURRENT_USER", dict(ASSIGNED_DOCTOR))

    app = Flask(__name__)
    app.register_blueprint(
        cr_module.clinical_records_bp, url_prefix="/api/clinical-records"
    )

    @app.errorhandler(auth.AuthError)
    def _on_auth_error(err):  # pragma: no cover - trivial
        return jsonify(error=err.message), err.status

    app.config.update(TESTING=True)
    return app.test_client()


# ==========================================================================
# 1. Successful creation when the admission is active.
# ==========================================================================
def test_post_creates_record_when_admission_active(client, db_mock, admission_active):
    """
    With require_active_for_create patched to succeed, POST / should:
      * call db.create_clinical_record exactly once with the whitelisted fields,
      * NOT forward updated_after_discharge (the route pops it), and
      * return 201 with the created row echoed back.
    """
    created_row = _make_record(record_id=10, admission_id=100)
    db_mock.create_clinical_record.return_value = created_row

    resp = client.post(
        "/api/clinical-records/",
        json={
            "patient_id": 55,
            "admission_id": 100,
            "doctor_id": ASSIGNED_DOCTOR["id"],
            "assessment_notes": "chest pain, ECG ordered",
            "updated_after_discharge": 1,  # attempt to forge the audit flag
        },
    )

    assert resp.status_code == 201
    assert resp.get_json()["clinical_record"] == created_row

    # The admission was checked, and the row was created once.
    admission_active.assert_called_once_with(100)
    db_mock.create_clinical_record.assert_called_once()

    sent = db_mock.create_clinical_record.call_args.args[0]
    assert sent["admission_id"] == 100
    assert sent["assessment_notes"] == "chest pain, ECG ordered"
    # The forged audit flag must have been stripped before the DB call.
    assert "updated_after_discharge" not in sent


# ==========================================================================
# 2. Blocked creation (409) when the admission is not active.
# ==========================================================================
def test_post_blocked_409_when_admission_not_active(client, db_mock, admission_inactive):
    """
    When require_active_for_create raises AdmissionNotActiveError, the route must
    translate that into HTTP 409 and must NOT touch the database at all.
    """
    resp = client.post(
        "/api/clinical-records/",
        json={
            "patient_id": 55,
            "admission_id": 100,
            "doctor_id": ASSIGNED_DOCTOR["id"],
            "assessment_notes": "late note",
        },
    )

    assert resp.status_code == 409
    assert "not active" in resp.get_json()["error"].lower()

    admission_inactive.assert_called_once_with(100)
    db_mock.create_clinical_record.assert_not_called()


# ==========================================================================
# 3. PUT sets updated_after_discharge = 1 when the admission has since ended.
# ==========================================================================
def test_put_flags_updated_after_discharge_when_admission_ended(
    client, db_mock, admission_inactive
):
    """
    The record already exists and the caller is its assigned doctor, so the PUT
    is allowed. Because the admission is now inactive (require_active_for_create
    raises), the route must add updated_after_discharge = 1 to the payload it
    sends to db.update_clinical_record - it must NOT refuse the update.
    """
    existing = _make_record(record_id=10, admission_id=100)
    db_mock.get_clinical_record.return_value = existing
    db_mock.update_clinical_record.return_value = {
        **existing,
        "care_plan": "discharge follow-up",
        "updated_after_discharge": 1,
    }

    resp = client.put(
        "/api/clinical-records/10",
        json={"care_plan": "discharge follow-up"},
    )

    assert resp.status_code == 200

    db_mock.update_clinical_record.assert_called_once()
    record_id_arg, payload = db_mock.update_clinical_record.call_args.args
    assert record_id_arg == 10
    assert payload["care_plan"] == "discharge follow-up"
    assert payload["updated_after_discharge"] == 1


# ==========================================================================
# 4. PUT keeps updated_after_discharge as 0 while the admission is still active.
# ==========================================================================
def test_put_does_not_flag_when_admission_still_active(
    client, db_mock, admission_active
):
    """
    Same PUT, but require_active_for_create succeeds (admission still 'Active').
    The route must send only the caller's fields and leave the audit flag alone
    - updated_after_discharge must not be added to the update payload.
    """
    existing = _make_record(record_id=10, admission_id=100)
    db_mock.get_clinical_record.return_value = existing
    db_mock.update_clinical_record.return_value = {
        **existing,
        "diagnosis_summary": "stable angina",
    }

    resp = client.put(
        "/api/clinical-records/10",
        json={"diagnosis_summary": "stable angina"},
    )

    assert resp.status_code == 200

    db_mock.update_clinical_record.assert_called_once()
    _, payload = db_mock.update_clinical_record.call_args.args
    assert payload.get("updated_after_discharge", 0) == 0
    assert "updated_after_discharge" not in payload


# ==========================================================================
# 5. DELETE results in status 'archived' rather than a removed row.
# ==========================================================================
def test_delete_soft_archives_and_keeps_row(client, db_mock, admission_active):
    """
    DELETE /<id> is a soft delete: the route calls db.update_clinical_record
    with {"status": "archived"} and must NEVER call db.delete_clinical_record.
    The response carries the still-present (now archived) row.
    """
    existing = _make_record(record_id=10, admission_id=100, status="active")
    archived = _make_record(record_id=10, admission_id=100, status="archived")
    db_mock.get_clinical_record.return_value = existing
    db_mock.update_clinical_record.return_value = archived

    resp = client.delete("/api/clinical-records/10")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["clinical_record"]["status"] == "archived"
    assert body["clinical_record"]["record_id"] == 10  # row still exists

    db_mock.update_clinical_record.assert_called_once_with(10, {"status": "archived"})
    db_mock.delete_clinical_record.assert_not_called()


# ==========================================================================
# 6. Admission-scoped GET returns only records for the requested admission.
# ==========================================================================
def test_admission_scoped_get_excludes_other_admissions_for_same_patient(
    client, db_mock
):
    """
    The same patient (id 55) has two clinical records:
      * record 10 under admission 100  (the one we ask for)
      * record 11 under admission 200  (a later, different admission)
    Both name the same assigned doctor, so both would pass the access check.
    GET /admission/100 must return record 10 only - never record 11 - proving
    the filter is by admission_id, not by patient.
    """
    rec_wanted = _make_record(record_id=10, admission_id=100, patient_id=55)
    rec_other = _make_record(record_id=11, admission_id=200, patient_id=55)
    db_mock.list_clinical_records.return_value = [rec_wanted, rec_other]

    resp = client.get("/api/clinical-records/admission/100")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["admission_id"] == 100

    returned_ids = {r["record_id"] for r in body["clinical_records"]}
    assert returned_ids == {10}
    assert all(r["admission_id"] == 100 for r in body["clinical_records"])
