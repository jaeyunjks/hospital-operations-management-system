"""
test_surgery_requests.py - behaviour tests for backend/routes/surgery_requests.py

The surgery_requests blueprint is exercised through a real Flask test client.
Every outbound dependency is mocked, so NO socket is ever opened:

  * services.database_client                       -> a MagicMock (row CRUD; port 6200)
  * services.external_services.get_available_theatre
    services.external_services.get_staff_details
    services.external_services.notify_room_and_bed  -> patched per test on the
    route module. surgery_requests.py imports these three names directly into
    its own namespace (`from services.external_services import ...`), so the
    patch target is `su_module.<name>`, not the services module.
  * services.admission_validation.require_active_for_create -> likewise imported
    by name into the route module; patched as `su_module.require_active_for_create`
    to either return None (admission active) or raise AdmissionNotActiveError
    (admission inactive).

auth.CURRENT_USER is pinned to the scheduling doctor for every test (the POST
route is @require_role("doctor")); doctor_id on the stored row is always taken
from that user, never from the request body.

Scenarios covered (one test each, per the task):
  1. Successful creation - theatre found, surgeon name resolved, Room & Bed
     accepts; the local row is created ONCE with the resolved bed_id and kept.
  2. Blocked creation (409) when the admission is inactive - nothing is written,
     and theatre selection / dispatch are never reached.
  3. get_available_theatre finds no theatre (ok + bed_id None) - 409, and NO
     local row is ever created, and notify_room_and_bed is never called.
  4. Theatre found and the local row already created, but notify_room_and_bed
     returns a 409-refusal result - the route deletes the row it just created
     and responds 409 with the refusal detail surfaced.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# --- Make the backend package importable ----------------------------------
BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "backend")
)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import auth  # noqa: E402
from routes import surgery_requests as su_module  # noqa: E402
from services.admission_validation import AdmissionNotActiveError  # noqa: E402


# Seed-data doctor. The POST route only ever serves a doctor, and doctor_id on
# the row is forced to this id regardless of the request body.
DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}

# A canned theatre bed_id, standing in for whatever Room & Bed's availability
# lookup would return. Deliberately distinct from any id in the request body so
# the "row stores the RESOLVED bed_id" assertion is meaningful.
STUB_BED_ID = 9001

# A valid POST body: the four fields the route requires. No doctor_id / bed_id -
# the route sets doctor_id from auth and bed_id from get_available_theatre.
VALID_BODY = {
    "patient_id": 2,
    "admission_id": 2,
    "procedure_type": "Laparoscopic appendectomy",
    "scheduled_at": "2026-09-10 09:00:00",
}


def _created_row(request_id=1, bed_id=STUB_BED_ID):
    """
    A surgery_requests row shaped like the database API returns it after a
    successful create - i.e. what db.create_surgery_request(...) hands back.
    """
    return {
        "request_id": request_id,
        "patient_id": VALID_BODY["patient_id"],
        "admission_id": VALID_BODY["admission_id"],
        "doctor_id": DOCTOR["id"],
        "bed_id": bed_id,
        "procedure_type": VALID_BODY["procedure_type"],
        "scheduled_at": VALID_BODY["scheduled_at"],
        "status": "scheduled",
        "created_at": "2026-09-03 08:00:00",
    }


# ==========================================================================
# Fixtures
# ==========================================================================
@pytest.fixture
def db_mock(monkeypatch):
    """
    Replace the database_client reference held by the route with a MagicMock.
    The genuine DatabaseClientError class is copied on so the route's
    `except db.DatabaseClientError` still catches. list_surgery_requests
    defaults to [] so an unset list read is harmless.
    """
    mock = MagicMock(name="database_client")
    mock.DatabaseClientError = su_module.db.DatabaseClientError
    mock.list_surgery_requests.return_value = []
    monkeypatch.setattr(su_module, "db", mock)
    return mock


@pytest.fixture
def admission_active(monkeypatch):
    """
    require_active_for_create -> returns None: the admission is 'Active', so the
    route proceeds past the admission gate.
    """
    stub = MagicMock(name="require_active_for_create", return_value=None)
    monkeypatch.setattr(su_module, "require_active_for_create", stub)
    return stub


@pytest.fixture
def admission_inactive(monkeypatch):
    """
    require_active_for_create -> raises AdmissionNotActiveError: the admission
    exists but is not active, so the route must block the create with 409.
    """
    def _raise(admission_id):
        raise AdmissionNotActiveError(admission_id, "Completed")

    stub = MagicMock(name="require_active_for_create", side_effect=_raise)
    monkeypatch.setattr(su_module, "require_active_for_create", stub)
    return stub


@pytest.fixture
def theatre_available(monkeypatch):
    """get_available_theatre -> a real bed_id ({"ok": True, "bed_id": 9001})."""
    stub = MagicMock(
        name="get_available_theatre",
        return_value={"ok": True, "bed_id": STUB_BED_ID},
    )
    monkeypatch.setattr(su_module, "get_available_theatre", stub)
    return stub


@pytest.fixture
def theatre_none_available(monkeypatch):
    """
    get_available_theatre -> the "call worked, nothing free" outcome
    ({"ok": True, "bed_id": None, "reason": "none_available"}).
    """
    stub = MagicMock(
        name="get_available_theatre",
        return_value={"ok": True, "bed_id": None, "reason": "none_available"},
    )
    monkeypatch.setattr(su_module, "get_available_theatre", stub)
    return stub


@pytest.fixture
def surgeon_name_resolves(monkeypatch):
    """
    get_staff_details -> a staff record with a usable full_name, so the route
    can resolve doctor_id -> surgeon_name for the Room & Bed dispatch.
    """
    stub = MagicMock(
        name="get_staff_details",
        return_value={
            "ok": True,
            "staff": {"staff_id": DOCTOR["id"], "full_name": "Dr Daniel Chen"},
        },
    )
    monkeypatch.setattr(su_module, "get_staff_details", stub)
    return stub


@pytest.fixture
def dispatch_accepted(monkeypatch):
    """notify_room_and_bed -> Room & Bed accepted the arrangement."""
    stub = MagicMock(
        name="notify_room_and_bed",
        return_value={"ok": True, "arrangement": {"purpose": "Surgery", "_stub": True}},
    )
    monkeypatch.setattr(su_module, "notify_room_and_bed", stub)
    return stub


@pytest.fixture
def dispatch_refused(monkeypatch):
    """
    notify_room_and_bed -> the distinct 409-refusal result
    ({"ok": False, "error": "refused", "detail": ...}). NOT a transport error.
    """
    stub = MagicMock(
        name="notify_room_and_bed",
        return_value={
            "ok": False,
            "error": "refused",
            "detail": "Theatre 1 is under maintenance at the requested time",
        },
    )
    monkeypatch.setattr(su_module, "notify_room_and_bed", stub)
    return stub


@pytest.fixture
def as_doctor(monkeypatch):
    """Pin auth.CURRENT_USER to the scheduling doctor for the test."""
    monkeypatch.setattr(auth, "CURRENT_USER", dict(DOCTOR))
    return DOCTOR


@pytest.fixture
def client(monkeypatch, db_mock):
    """Flask test client with only the surgery_requests blueprint mounted."""
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(
        su_module.surgery_requests_bp, url_prefix="/api/surgery-requests"
    )
    app.config.update(TESTING=True)
    return app.test_client()


# ==========================================================================
# 1. Successful creation.
# ==========================================================================
def test_successful_creation_stores_resolved_bed_id_and_keeps_row(
    client,
    db_mock,
    as_doctor,
    admission_active,
    theatre_available,
    surgeon_name_resolves,
    dispatch_accepted,
):
    """
    Happy path end to end:
      * admission is active (gate passes),
      * get_available_theatre returns bed_id 9001,
      * get_staff_details resolves the surgeon's name,
      * notify_room_and_bed accepts.

    Asserts:
      * response is 201 and echoes back both the stored row and the Room & Bed
        arrangement;
      * db.create_surgery_request was called EXACTLY once (one local write),
        and the payload it received carries bed_id == 9001 (the RESOLVED id,
        not anything from the request body) plus doctor_id from auth and
        status 'scheduled';
      * notify_room_and_bed was handed that same resolved bed_id and a NAME
        for the surgeon (not the numeric id);
      * the row is NOT rolled back - db.delete_surgery_request is never called.
    """
    db_mock.create_surgery_request.return_value = _created_row(request_id=1)

    resp = client.post("/api/surgery-requests/", json=VALID_BODY)

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["surgery_request"]["bed_id"] == STUB_BED_ID
    assert body["surgery_request"]["request_id"] == 1
    assert body["room_and_bed"] is not None

    # Exactly one local write, with the resolved bed_id and auth-derived doctor_id.
    db_mock.create_surgery_request.assert_called_once()
    stored = db_mock.create_surgery_request.call_args.args[0]
    assert stored["bed_id"] == STUB_BED_ID
    assert stored["doctor_id"] == DOCTOR["id"]
    assert stored["status"] == "scheduled"

    # Dispatch received the resolved bed_id and a surgeon NAME, not an id.
    dispatch_args = su_module.notify_room_and_bed.call_args.args
    assert dispatch_args[1] == "Dr Daniel Chen"   # surgeon_name
    assert dispatch_args[2] == STUB_BED_ID        # bed_id

    # Accepted -> no rollback.
    db_mock.delete_surgery_request.assert_not_called()


# ==========================================================================
# 2. Blocked creation (409) when the admission is inactive.
# ==========================================================================
def test_creation_blocked_409_when_admission_inactive(
    client,
    db_mock,
    as_doctor,
    admission_inactive,
    theatre_available,
    surgeon_name_resolves,
    dispatch_accepted,
):
    """
    require_active_for_create raises AdmissionNotActiveError. The route must
    stop at the admission gate:
      * response is 409 and the message names the reported status ('Completed');
      * NO local row is created;
      * theatre selection and Room & Bed dispatch are never reached
        (get_available_theatre / notify_room_and_bed not called), proving the
        gate is the first thing that runs after auth + payload validation.

    The theatre/surgeon/dispatch fixtures are wired to "succeed" on purpose, so
    the 409 can only be coming from the admission check, not from them.
    """

    resp = client.post("/api/surgery-requests/", json=VALID_BODY)

    assert resp.status_code == 409
    assert "not active" in resp.get_json()["error"]
    assert "Completed" in resp.get_json()["error"]

    db_mock.create_surgery_request.assert_not_called()
    su_module.get_available_theatre.assert_not_called()
    su_module.notify_room_and_bed.assert_not_called()


# ==========================================================================
# 3. get_available_theatre finds no theatre available.
# ==========================================================================
def test_no_theatre_available_returns_409_and_writes_nothing(
    client,
    db_mock,
    as_doctor,
    admission_active,
    theatre_none_available,
    surgeon_name_resolves,
    dispatch_accepted,
):
    """
    The admission is active, but get_available_theatre returns
    {"ok": True, "bed_id": None, "reason": "none_available"} - the lookup
    worked, there is simply no capacity.

    Asserts:
      * response is 409 (a clean "can't satisfy this right now", not a 5xx),
        the message makes clear nothing was saved, and reason == 'none_available';
      * db.create_surgery_request is NEVER called - no row is created without a
        confirmed bed_id;
      * notify_room_and_bed is NEVER called - the constraint that dispatch only
        happens after a bed_id is resolved holds.
    """

    resp = client.post("/api/surgery-requests/", json=VALID_BODY)

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["reason"] == "none_available"
    assert "not created" in body["error"]

    db_mock.create_surgery_request.assert_not_called()
    su_module.notify_room_and_bed.assert_not_called()


# ==========================================================================
# 4. Bed found, local row created, but notify_room_and_bed returns a
#    409-refusal result.
# ==========================================================================
def test_room_and_bed_refusal_rolls_back_the_created_row(
    client,
    db_mock,
    as_doctor,
    admission_active,
    theatre_available,
    surgeon_name_resolves,
    dispatch_refused,
):
    """
    Everything up to and including the local write succeeds:
      * admission active, theatre bed 9001 chosen, surgeon name resolved,
      * db.create_surgery_request returns row id 1,
    then notify_room_and_bed returns the DISTINCT refusal result
    ({"ok": False, "error": "refused", "detail": ...}) - a definitive 409 from
    Room & Bed, not a transport failure.

    Asserts (per the route's documented reasoning):
      * the row WAS created first (create called once), proving this is the
        "row already exists" path and not an earlier bail-out;
      * the route then DELETES that exact row - db.delete_surgery_request(1);
      * response is 409 (matching Room & Bed's own refusal semantics),
        reason == 'refused', and the refusal detail is surfaced verbatim in
        room_and_bed_detail so the doctor knows what to change;
      * the response body says the request was not saved.
    """
    db_mock.create_surgery_request.return_value = _created_row(request_id=1)

    resp = client.post("/api/surgery-requests/", json=VALID_BODY)

    # The local row was created before the dispatch was attempted...
    db_mock.create_surgery_request.assert_called_once()
    # ...and then rolled back by id once the refusal came back.
    db_mock.delete_surgery_request.assert_called_once_with(1)

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["reason"] == "refused"
    assert body["room_and_bed_detail"] == (
        "Theatre 1 is under maintenance at the requested time"
    )
    assert "not saved" in body["error"]
