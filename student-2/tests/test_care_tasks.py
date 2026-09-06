"""
test_care_tasks.py - behaviour tests for backend/routes/care_tasks.py

The care_tasks blueprint is exercised through a real Flask test client, but the
only outbound dependency - services.database_client (the REST client for the
database container on port 6200) - is replaced with a MagicMock. No socket is
ever opened.

care_tasks.py deliberately has NO admission-status check: there is no
admission_validation import to patch. "Complete works even if the admission is
inactive" therefore just means "complete succeeds with nothing standing in the
way" - the test proves the code path never consults admission state.

Authorisation is driven by auth.CURRENT_USER, which we set per test to a
doctor / nurse / specialist / wrong-nurse as each scenario needs.

Scenarios covered (>= 1 test each):
  1. Normal CRUD - create, read (list + one), plain edit (PUT).
  2. Cancel = soft delete: DELETE PUTs status -> 'cancelled', never removes the
     row, never calls db.delete_care_task.
  3. Acknowledge / complete transitions, and that they are nurse-only
     (a doctor hitting them is 403).
  4. Specialists cannot touch care tasks in ANY form (every verb -> 403).
  5. Both a doctor AND a nurse can cancel a task.
  6. A task can be completed even though its linked admission is no longer
     active (no admission check exists in this route).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# --- Make the backend package importable -----------------------------------
# care_tasks.py does `from services import database_client as db` and
# `from auth import ...`; those only resolve with backend/ on sys.path.
BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "backend")
)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import auth  # noqa: E402
from routes import care_tasks as ct_module  # noqa: E402


# Seed-data users we switch between via auth.CURRENT_USER.
DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}
NURSE = {"id": 7, "name": "James Wilson", "role": "nurse"}
OTHER_NURSE = {"id": 8, "name": "Aisha Khan", "role": "nurse"}
SPECIALIST = {"id": 3, "name": "Dr Emily Brown", "role": "specialist"}


def _make_task(task_id=20, status="pending", assigned_nurse_id=NURSE["id"]):
    """A care_tasks row shaped like the database API returns it."""
    return {
        "task_id": task_id,
        "clinical_record_id": 10,
        "assigned_nurse_id": assigned_nurse_id,
        "task_description": "2-hourly obs",
        "notes": None,
        "due_at": "2026-09-02T09:00:00Z",
        "status": status,
        "completed_at": None,
    }


@pytest.fixture
def db_mock(monkeypatch):
    """
    Swap the database_client reference the route holds for a MagicMock. The real
    DatabaseClientError class is copied on so `except db.DatabaseClientError`
    still catches. list_care_tasks defaults to [] so unfiltered reads are safe.
    """
    mock = MagicMock(name="database_client")
    mock.DatabaseClientError = ct_module.db.DatabaseClientError
    mock.list_care_tasks.return_value = []
    monkeypatch.setattr(ct_module, "db", mock)
    return mock


@pytest.fixture
def as_user(monkeypatch):
    """
    Return a setter that pins auth.CURRENT_USER to a given dict. Tests call
    as_user(DOCTOR) / as_user(NURSE) / ... to choose who is "logged in".
    """
    def _set(user):
        monkeypatch.setattr(auth, "CURRENT_USER", dict(user))
        return user

    return _set


@pytest.fixture
def client(monkeypatch, db_mock):
    """
    Flask test client with only the care_tasks blueprint mounted and its
    AuthError handler active. db_mock is pulled in so patching happens first.
    """
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(ct_module.care_tasks_bp, url_prefix="/api/care-tasks")
    app.config.update(TESTING=True)
    return app.test_client()


# ==========================================================================
# 1. Normal CRUD - create / read / edit.
# ==========================================================================
def test_crud_create_read_edit(client, db_mock, as_user):
    """
    Walks the happy path a doctor + nurse would take:
      * doctor POSTs a new task -> 201, status forced to 'pending', and the
        client's attempt to preset status/completed_at is ignored;
      * GET / lists it and GET /<id> fetches it;
      * a plain PUT updates the free-text fields while the task is still open.
    Each step asserts the exact db.* call the route should have made.
    """
    as_user(DOCTOR)
    parent_record = {"record_id": 10, "patient_id": 55}
    db_mock.get_clinical_record.return_value = parent_record
    created = _make_task(task_id=20, status="pending")
    db_mock.create_care_task.return_value = created

    resp = client.post(
        "/api/care-tasks/",
        json={
            "clinical_record_id": 10,
            "assigned_nurse_id": NURSE["id"],
            "task_description": "2-hourly obs",
            "status": "completed",      # must be ignored
            "completed_at": "2020-01-01T00:00:00Z",  # must be ignored
        },
    )
    assert resp.status_code == 201
    assert resp.get_json()["care_task"] == created
    sent = db_mock.create_care_task.call_args.args[0]
    assert sent["status"] == "pending"
    assert sent["completed_at"] is None
    assert "status" in sent and sent["status"] != "completed"

    # READ - list
    db_mock.list_care_tasks.return_value = [created]
    resp = client.get("/api/care-tasks/")
    assert resp.status_code == 200
    assert resp.get_json()["care_tasks"] == [created]

    # READ - one
    db_mock.get_care_task.return_value = created
    resp = client.get("/api/care-tasks/20")
    assert resp.status_code == 200
    assert resp.get_json()["care_task"] == created

    # UPDATE - plain edit of an open task
    db_mock.update_care_task.return_value = {**created, "notes": "family informed"}
    resp = client.put("/api/care-tasks/20", json={"notes": "family informed"})
    assert resp.status_code == 200
    db_mock.update_care_task.assert_called_once_with(20, {"notes": "family informed"})


# ==========================================================================
# 2. Cancel is a soft delete.
# ==========================================================================
def test_cancel_is_soft_delete_keeps_row(client, db_mock, as_user):
    """
    DELETE /<id> must translate to db.update_care_task(id, {"status":"cancelled"})
    and must never call db.delete_care_task. The row comes back in the response,
    proving it still exists.
    """
    as_user(DOCTOR)
    task = _make_task(task_id=20, status="pending")
    cancelled = _make_task(task_id=20, status="cancelled")
    db_mock.get_care_task.return_value = task
    db_mock.update_care_task.return_value = cancelled

    resp = client.delete("/api/care-tasks/20")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["care_task"]["status"] == "cancelled"
    assert body["care_task"]["task_id"] == 20
    db_mock.update_care_task.assert_called_once_with(20, {"status": "cancelled"})
    db_mock.delete_care_task.assert_not_called()


# ==========================================================================
# 3a. Acknowledge + complete transitions succeed for the assigned nurse.
# ==========================================================================
def test_nurse_acknowledge_then_complete(client, db_mock, as_user):
    """
    The assigned nurse (id 7) drives the workflow:
      * PUT /<id>/acknowledge : pending -> acknowledged
      * PUT /<id>/complete    : acknowledged -> completed, completed_at stamped
    Asserts the status the route wrote and that completed_at was set on complete.
    """
    as_user(NURSE)

    pending = _make_task(task_id=20, status="pending", assigned_nurse_id=NURSE["id"])
    db_mock.get_care_task.return_value = pending
    db_mock.update_care_task.return_value = {**pending, "status": "acknowledged"}

    resp = client.put("/api/care-tasks/20/acknowledge")
    assert resp.status_code == 200
    db_mock.update_care_task.assert_called_with(20, {"status": "acknowledged"})

    acked = _make_task(task_id=20, status="acknowledged", assigned_nurse_id=NURSE["id"])
    db_mock.get_care_task.return_value = acked
    db_mock.update_care_task.return_value = {**acked, "status": "completed"}

    resp = client.put("/api/care-tasks/20/complete", json={"notes": "obs stable"})
    assert resp.status_code == 200
    task_id_arg, changes = db_mock.update_care_task.call_args.args
    assert task_id_arg == 20
    assert changes["status"] == "completed"
    assert changes["completed_at"]  # a timestamp was stamped
    assert changes["notes"] == "obs stable"


# ==========================================================================
# 3b. Acknowledge / complete are nurse-only - a doctor is rejected.
# ==========================================================================
def test_doctor_cannot_acknowledge_or_complete(client, db_mock, as_user):
    """
    Both action routes are decorated with require_role("nurse"). A doctor
    calling either must get 403 and the database must be left untouched
    (require_role fires before the handler and before require_assignment).
    """
    as_user(DOCTOR)
    db_mock.get_care_task.return_value = _make_task(task_id=20, status="pending")

    ack = client.put("/api/care-tasks/20/acknowledge")
    done = client.put("/api/care-tasks/20/complete")

    assert ack.status_code == 403
    assert done.status_code == 403
    db_mock.update_care_task.assert_not_called()


# ==========================================================================
# 3c. Acknowledge / complete are the ASSIGNED nurse's alone.
# ==========================================================================
def test_other_nurse_cannot_acknowledge(client, db_mock, as_user):
    """
    require_assignment(_assigned_nurse_id) compares care_tasks.assigned_nurse_id
    (7) to the caller. Nurse 8 is a valid clinical role but not the assignee, so
    acknowledge must return 403 and never write.
    """
    as_user(OTHER_NURSE)
    db_mock.get_care_task.return_value = _make_task(
        task_id=20, status="pending", assigned_nurse_id=NURSE["id"]
    )

    resp = client.put("/api/care-tasks/20/acknowledge")

    assert resp.status_code == 403
    db_mock.update_care_task.assert_not_called()


# ==========================================================================
# 4. Specialists have NO access to care tasks in any form.
# ==========================================================================
@pytest.mark.parametrize(
    "method, path, send_json",
    [
        ("get", "/api/care-tasks/", False),
        ("get", "/api/care-tasks/20", False),
        ("post", "/api/care-tasks/", True),
        ("put", "/api/care-tasks/20", True),
        ("delete", "/api/care-tasks/20", False),
        ("put", "/api/care-tasks/20/acknowledge", False),
        ("put", "/api/care-tasks/20/complete", False),
    ],
)
def test_specialist_is_blocked_on_every_route(
    client, db_mock, as_user, method, path, send_json
):
    """
    Every route in care_tasks.py is restricted to doctor/nurse (never the bare
    require_role() that would also admit a specialist). Logged in as a
    specialist, each verb+path must return 403 and must not reach the database.
    """
    as_user(SPECIALIST)
    db_mock.get_care_task.return_value = _make_task(task_id=20)

    kwargs = {"json": {}} if send_json else {}
    resp = getattr(client, method)(path, **kwargs)

    assert resp.status_code == 403
    db_mock.create_care_task.assert_not_called()
    db_mock.update_care_task.assert_not_called()
    db_mock.delete_care_task.assert_not_called()


# ==========================================================================
# 5. Both doctors and nurses can cancel a care task.
# ==========================================================================
@pytest.mark.parametrize("actor", [DOCTOR, NURSE], ids=["doctor", "nurse"])
def test_doctor_and_nurse_can_both_cancel(client, db_mock, as_user, actor):
    """
    DELETE /<id> is a shared doctor/nurse privilege. Run once as the doctor and
    once as the nurse; both must get 200 and both must produce the same
    soft-delete write (status -> 'cancelled').
    """
    as_user(actor)
    task = _make_task(task_id=20, status="acknowledged")
    db_mock.get_care_task.return_value = task
    db_mock.update_care_task.return_value = _make_task(task_id=20, status="cancelled")

    resp = client.delete("/api/care-tasks/20")

    assert resp.status_code == 200
    assert resp.get_json()["care_task"]["status"] == "cancelled"
    db_mock.update_care_task.assert_called_once_with(20, {"status": "cancelled"})
    db_mock.delete_care_task.assert_not_called()


# ==========================================================================
# 6. Completion succeeds even when the linked admission is no longer active.
# ==========================================================================
def test_complete_succeeds_when_admission_inactive(client, db_mock, as_user):
    """
    care_tasks.py has no admission_validation import and no admission check, so
    an ended admission must not matter. We assert this two ways:
      * the module genuinely has no admission-check symbol wired in
        (if someone later adds an admission gate here, this line fails), and
      * the assigned nurse completing a task whose linked admission is
        'Completed' still gets 200 with completed_at stamped.
    """
    # The route file must not import or reference an admission validator.
    assert not hasattr(ct_module, "require_active_for_create")
    assert not hasattr(ct_module, "cancel_if_inactive")

    as_user(NURSE)
    acked = _make_task(task_id=20, status="acknowledged", assigned_nurse_id=NURSE["id"])
    db_mock.get_care_task.return_value = acked
    db_mock.update_care_task.return_value = {**acked, "status": "completed"}

    resp = client.put("/api/care-tasks/20/complete")

    assert resp.status_code == 200
    _, changes = db_mock.update_care_task.call_args.args
    assert changes["status"] == "completed"
    assert changes["completed_at"]
