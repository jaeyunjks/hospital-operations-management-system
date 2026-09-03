"""
test_consultations.py - behaviour tests for backend/routes/consultations.py

The consultations blueprint is exercised through a real Flask test client. The
two outbound HTTP dependencies are mocked, so no socket is ever opened:

  * services.database_client        -> a MagicMock (row CRUD; port 6200)
  * services.admission_validation.cancel_if_inactive -> patched per test to
    say the linked admission is still active (returns False) or has gone
    inactive (returns True). This is the ONLY admission hook consultations.py
    uses; require_active_for_create is not imported here.

auth.CURRENT_USER is set per test to the requesting doctor, the addressed
specialist, or an unrelated doctor/specialist, to drive the ownership rules.

Scenarios covered (>= 1 test each):
  1. Normal CRUD - create, list, read-one, edit reason, (cancel covered below).
  2. Cancel while status == 'requested'            -> 200, status 'cancelled'.
  3. Cancel while status == 'in_review'            -> 409, no write.
  4. A doctor can only reach their OWN submitted requests (someone else's -> 403,
     and the list is filtered to theirs).
  5. A specialist can only reach requests addressed to THEM (others' -> 403).
  6. respond flow, admission still active  -> recommendation recorded + status
     'completed'.
  7. respond flow, admission gone inactive -> status 'cancelled', NO
     recommendation written.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# --- Make the backend package importable -----------------------------------
BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "backend")
)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import auth  # noqa: E402
from routes import consultations as co_module  # noqa: E402


# Seed-data users.
DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}       # requesting doctor
OTHER_DOCTOR = {"id": 2, "name": "Dr Priya Nair", "role": "doctor"}  # not on the request
SPECIALIST = {"id": 3, "name": "Dr Emily Brown", "role": "specialist"}       # addressed
OTHER_SPECIALIST = {"id": 4, "name": "Dr Sam Lee", "role": "specialist"}     # not addressed


def _make_request(request_id=30, status="requested"):
    """A consultation_requests row shaped like the database API returns it."""
    return {
        "request_id": request_id,
        "clinical_record_id": 10,
        "patient_id": 55,
        "admission_id": 100,
        "requesting_doctor_id": DOCTOR["id"],
        "specialist_id": SPECIALIST["id"],
        "reason_for_request": "?cardiology review",
        "recommendation": None,
        "status": status,
        "completed_at": None,
    }


@pytest.fixture
def db_mock(monkeypatch):
    """
    Replace the database_client reference held by the route with a MagicMock.
    The genuine DatabaseClientError class is copied on so the route's
    `except db.DatabaseClientError` still works. list_consultation_requests
    defaults to [] so an unset list read is harmless.
    """
    mock = MagicMock(name="database_client")
    mock.DatabaseClientError = co_module.db.DatabaseClientError
    mock.list_consultation_requests.return_value = []
    monkeypatch.setattr(co_module, "db", mock)
    return mock


@pytest.fixture
def admission_active(monkeypatch):
    """
    cancel_if_inactive -> False: the linked admission is still 'Active', so no
    auto-cancel happens anywhere in the route.
    """
    stub = MagicMock(name="cancel_if_inactive", return_value=False)
    monkeypatch.setattr(co_module, "cancel_if_inactive", stub)
    return stub


@pytest.fixture
def admission_inactive(monkeypatch):
    """
    cancel_if_inactive -> True: the admission has gone inactive, so any open
    request the route touches must be auto-cancelled.
    """
    stub = MagicMock(name="cancel_if_inactive", return_value=True)
    monkeypatch.setattr(co_module, "cancel_if_inactive", stub)
    return stub


@pytest.fixture
def as_user(monkeypatch):
    """Setter that pins auth.CURRENT_USER for the duration of one test."""
    def _set(user):
        monkeypatch.setattr(auth, "CURRENT_USER", dict(user))
        return user

    return _set


@pytest.fixture
def client(monkeypatch, db_mock):
    """Flask test client with only the consultations blueprint mounted."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(co_module.consultations_bp, url_prefix="/api/consultations")
    app.config.update(TESTING=True)
    return app.test_client()


# ==========================================================================
# 1. Normal CRUD - create / list / read-one / edit.
# ==========================================================================
def test_crud_create_list_read_edit(client, db_mock, as_user, admission_active):
    """
    Doctor's happy path:
      * POST : record exists and is the doctor's own, admission active ->
        201, and requesting_doctor_id in the stored payload comes from the
        current user (a doctor_id in the body would be ignored - not sent here).
      * GET /       : returns the doctor's own request.
      * GET /<id>   : returns that one request (doctor view does not change
        status; only a specialist's first view moves 'requested' -> 'in_review').
      * PUT /<id>   : edits reason_for_request while still 'requested'.
    """
    as_user(DOCTOR)

    record = {"record_id": 10, "patient_id": 55, "admission_id": 100, "doctor_id": DOCTOR["id"]}
    db_mock.get_clinical_record.return_value = record
    created = _make_request(request_id=30, status="requested")
    db_mock.create_consultation_request.return_value = created

    resp = client.post(
        "/api/consultations/",
        json={
            "clinical_record_id": 10,
            "specialist_id": SPECIALIST["id"],
            "reason_for_request": "?cardiology review",
        },
    )
    assert resp.status_code == 201
    payload = db_mock.create_consultation_request.call_args.args[0]
    assert payload["requesting_doctor_id"] == DOCTOR["id"]  # from auth, not body
    assert payload["status"] == "requested"

    # LIST - only the caller's own rows come back.
    db_mock.list_consultation_requests.return_value = [created]
    resp = client.get("/api/consultations/")
    assert resp.status_code == 200
    assert resp.get_json()["consultation_requests"] == [created]

    # READ ONE - doctor view leaves status alone.
    db_mock.get_consultation_request.return_value = created
    resp = client.get("/api/consultations/30")
    assert resp.status_code == 200
    assert resp.get_json()["consultation_request"]["status"] == "requested"

    # EDIT - reason change while 'requested'.
    db_mock.update_consultation_request.return_value = {
        **created, "reason_for_request": "?arrhythmia"
    }
    resp = client.put("/api/consultations/30", json={"reason_for_request": "?arrhythmia"})
    assert resp.status_code == 200
    db_mock.update_consultation_request.assert_called_with(
        30, {"reason_for_request": "?arrhythmia"}
    )


# ==========================================================================
# 2. Cancel while the request is still 'requested'.
# ==========================================================================
def test_cancel_when_status_requested(client, db_mock, as_user, admission_active):
    """
    DELETE /<id> by the requesting doctor on a 'requested' request:
    the admission is still active (no auto-cancel), so the route issues its own
    update to status -> 'cancelled', keeps the row, and returns 200.
    """
    as_user(DOCTOR)
    row = _make_request(request_id=30, status="requested")
    db_mock.get_consultation_request.return_value = row
    db_mock.update_consultation_request.return_value = _make_request(
        request_id=30, status="cancelled"
    )

    resp = client.delete("/api/consultations/30")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["consultation_request"]["status"] == "cancelled"
    assert body["consultation_request"]["request_id"] == 30  # row kept
    db_mock.update_consultation_request.assert_called_once_with(30, {"status": "cancelled"})
    db_mock.delete_consultation_request.assert_not_called()


# ==========================================================================
# 3. Cancel while the request is 'in_review'.
# ==========================================================================
def test_cancel_when_status_in_review_is_refused(client, db_mock, as_user, admission_active):
    """
    Once a specialist has picked the request up ('in_review'), the doctor can no
    longer withdraw it. DELETE /<id> must return 409 and write nothing - the
    admission is active so the only possible write would be the doctor's cancel,
    which the status guard blocks.
    """
    as_user(DOCTOR)
    db_mock.get_consultation_request.return_value = _make_request(
        request_id=30, status="in_review"
    )

    resp = client.delete("/api/consultations/30")

    assert resp.status_code == 409
    assert "in_review" in resp.get_json()["error"]
    db_mock.update_consultation_request.assert_not_called()


# ==========================================================================
# 4. A doctor can only access their own submitted requests.
# ==========================================================================
def test_doctor_cannot_access_another_doctors_request(
    client, db_mock, as_user, admission_active
):
    """
    The request's requesting_doctor_id is 1. Logged in as doctor 2:
      * GET /<id> -> 403 (not party to it)
      * GET /     -> the request is filtered out, so an empty list
    Proves ownership is enforced on both the single-item and list routes.
    """
    as_user(OTHER_DOCTOR)
    row = _make_request(request_id=30, status="requested")
    db_mock.get_consultation_request.return_value = row
    db_mock.list_consultation_requests.return_value = [row]

    one = client.get("/api/consultations/30")
    assert one.status_code == 403

    listing = client.get("/api/consultations/")
    assert listing.status_code == 200
    assert listing.get_json()["consultation_requests"] == []


# ==========================================================================
# 5. A specialist can only access requests sent to them.
# ==========================================================================
def test_specialist_cannot_access_request_not_addressed_to_them(
    client, db_mock, as_user, admission_active
):
    """
    The request's specialist_id is 3. Logged in as specialist 4:
      * GET /<id>        -> 403
      * PUT /<id>/respond-> 403 (with a valid recommendation body, so the 403
        is the ownership check, not the missing-field check)
      * GET /            -> filtered to empty
    """
    as_user(OTHER_SPECIALIST)
    row = _make_request(request_id=30, status="requested")
    db_mock.get_consultation_request.return_value = row
    db_mock.list_consultation_requests.return_value = [row]

    assert client.get("/api/consultations/30").status_code == 403

    respond = client.put(
        "/api/consultations/30/respond", json={"recommendation": "start beta-blocker"}
    )
    assert respond.status_code == 403

    listing = client.get("/api/consultations/")
    assert listing.get_json()["consultation_requests"] == []
    db_mock.update_consultation_request.assert_not_called()


# ==========================================================================
# 6. respond flow - admission still active.
# ==========================================================================
def test_respond_records_recommendation_and_completes_when_admission_active(
    client, db_mock, as_user, admission_active
):
    """
    The addressed specialist responds to an open ('in_review') request while the
    admission is still active. The route must write ONE update carrying:
    recommendation text, status -> 'completed', and a completed_at timestamp.
    Response is 200 with the completed row.
    """
    as_user(SPECIALIST)
    row = _make_request(request_id=30, status="in_review")
    db_mock.get_consultation_request.return_value = row
    db_mock.update_consultation_request.return_value = {
        **row,
        "recommendation": "start beta-blocker, review in 2 weeks",
        "status": "completed",
        "completed_at": "2026-09-01T10:00:00Z",
    }

    resp = client.put(
        "/api/consultations/30/respond",
        json={"recommendation": "start beta-blocker, review in 2 weeks"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["consultation_request"]["status"] == "completed"

    db_mock.update_consultation_request.assert_called_once()
    req_id_arg, changes = db_mock.update_consultation_request.call_args.args
    assert req_id_arg == 30
    assert changes["recommendation"] == "start beta-blocker, review in 2 weeks"
    assert changes["status"] == "completed"
    assert changes["completed_at"]


# ==========================================================================
# 7. respond flow - admission has since become inactive.
# ==========================================================================
def test_respond_auto_cancels_and_records_no_recommendation_when_admission_inactive(
    client, db_mock, as_user, admission_inactive
):
    """
    Same responder and request, but cancel_if_inactive now returns True. The
    route runs the admission check FIRST: it auto-cancels the request (one
    update: status -> 'cancelled'), then sees the request is closed and returns
    409 - WITHOUT ever writing the recommendation.

    Assertions:
      * response is 409 mentioning 'cancelled';
      * exactly one update was made, and it was {"status": "cancelled"};
      * no update carrying a 'recommendation' key was ever sent.
    """
    as_user(SPECIALIST)
    row = _make_request(request_id=30, status="in_review")
    db_mock.get_consultation_request.return_value = row
    db_mock.update_consultation_request.return_value = _make_request(
        request_id=30, status="cancelled"
    )

    resp = client.put(
        "/api/consultations/30/respond",
        json={"recommendation": "start beta-blocker"},
    )

    assert resp.status_code == 409
    assert "cancelled" in resp.get_json()["error"]

    db_mock.update_consultation_request.assert_called_once_with(30, {"status": "cancelled"})
    for call in db_mock.update_consultation_request.call_args_list:
        assert "recommendation" not in call.args[1]
