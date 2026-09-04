"""
test_ai_summary.py - behaviour tests for backend/routes/ai_summary.py

Same pattern as the other route tests in this folder: the blueprint runs in a
real Flask test client, but every outbound dependency is replaced.

  * services.database_client -> a MagicMock (no socket to the DB container).
  * services.ollama_client   -> a MagicMock we drive per test. Its real
                                FALLBACK_SUMMARY string and OLLAMA_MODEL name
                                are copied onto the mock so the route's
                                `summary_text == ollama_client.FALLBACK_SUMMARY`
                                check and its `model_used` payload still work.

Authorisation is driven by auth.CURRENT_USER, set per test via the `as_user`
fixture to a doctor / nurse / specialist / unrelated staff member.

Scenarios covered (>= 1 test each):
  1. Doctor, 'clinical' scope: summary generated + logged to ai_summaries.
  2. Nurse, 'care_tasks' scope: summary generated + logged.
  3. Nurse asking for 'clinical' or 'consultation': rejected (403), not
     downgraded, and nothing is logged.
  4. Admission with no clinical records / no care tasks: a clear error, no
     empty or broken summary row written.
  5. Ollama returns its fallback value: a row is STILL created in ai_summaries
     (the attempt is logged), flagged ai_unavailable.
  6. Review route sets review_status to 'accepted' / 'edited' / 'rejected'.
  7. Review route rejects setting review_status back to 'pending'.
  8. Review route rejects a staff member who is neither the original requester
     nor assigned to the patient/admission.
  9. Review route never touches summary_text / model_used / admission_id /
     summary_scope - only review_status + reviewed_by_staff_id move.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# --- Make the backend package importable -----------------------------------
# ai_summary.py does `from services import database_client as db`,
# `from services import ollama_client`, and `from auth import ...`; those only
# resolve with backend/ on sys.path.
BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "backend")
)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import auth  # noqa: E402
from routes import ai_summary as ai_module  # noqa: E402


# Seed-data users we switch between via auth.CURRENT_USER.
DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}
OTHER_DOCTOR = {"id": 2, "name": "Dr Priya Nair", "role": "doctor"}
SPECIALIST = {"id": 3, "name": "Dr Emily Brown", "role": "specialist"}
NURSE = {"id": 7, "name": "James Wilson", "role": "nurse"}
OTHER_NURSE = {"id": 8, "name": "Aisha Khan", "role": "nurse"}

# The fallback string / model name the route compares against. We keep our own
# copies here so the tests read clearly and don't depend on import order.
FALLBACK = "No AI summary could be generated (summary service unavailable)."
MODEL_NAME = "llama3.1:8b"

ADMISSION_ID = 40
PATIENT_ID = 55
RECORD_ID = 10


# ------------------------------------------------------------
# Row builders - shaped like the database API returns them.
# ------------------------------------------------------------
def _clinical_record(record_id=RECORD_ID, doctor_id=DOCTOR["id"], admission_id=ADMISSION_ID):
    return {
        "record_id": record_id,
        "patient_id": PATIENT_ID,
        "admission_id": admission_id,
        "doctor_id": doctor_id,
        "assessment_notes": "Productive cough, fever 38.9, crackles right base.",
        "diagnosis_summary": "Community-acquired pneumonia.",
        "care_plan": "IV ceftriaxone, 4-hourly obs, review in 48h.",
        "status": "open",
    }


def _care_task(task_id=20, nurse_id=NURSE["id"], record_id=RECORD_ID):
    return {
        "task_id": task_id,
        "clinical_record_id": record_id,
        "assigned_nurse_id": nurse_id,
        "task_description": "Administer IV ceftriaxone",
        "notes": "Given 08:00, no reaction.",
        "status": "completed",
        "due_at": None,
        "completed_at": "2026-09-03T08:00:00Z",
    }


def _summary_row(
    summary_id=1,
    scope="clinical",
    summary_text="AI: pneumonia, on IV ceftriaxone, obs stable.",
    review_status="pending",
    source_reference="requested_by_staff_id={}".format(DOCTOR["id"]),
    reviewed_by_staff_id=None,
):
    """An ai_summaries row as the database API would hand it back."""
    return {
        "summary_id": summary_id,
        "admission_id": ADMISSION_ID,
        "patient_id": PATIENT_ID,
        "summary_text": summary_text,
        "model_used": MODEL_NAME,
        "source_reference": source_reference,
        "summary_scope": scope,
        "generated_at": "2026-09-03T09:00:00Z",
        "reviewed_by_staff_id": reviewed_by_staff_id,
        "review_status": review_status,
    }


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------
@pytest.fixture
def db_mock(monkeypatch):
    """
    Replace the database_client reference the route holds with a MagicMock. The
    real DatabaseClientError class is copied on so `except db.DatabaseClientError`
    still catches. The three list_* helpers default to [] so unfiltered reads
    are safe until a test says otherwise.
    """
    mock = MagicMock(name="database_client")
    mock.DatabaseClientError = ai_module.db.DatabaseClientError
    mock.list_clinical_records.return_value = []
    mock.list_consultation_requests.return_value = []
    mock.list_care_tasks.return_value = []
    # create_ai_summary echoes back the payload plus a generated id by default.
    mock.create_ai_summary.side_effect = lambda payload: {"summary_id": 1, **payload}
    monkeypatch.setattr(ai_module, "db", mock)
    return mock


@pytest.fixture
def ollama_mock(monkeypatch):
    """
    Replace the ollama_client reference with a MagicMock. Its real constants are
    copied on so the route's fallback comparison and model_used stamp behave.
    By default it returns a normal (non-fallback) summary string.
    """
    mock = MagicMock(name="ollama_client")
    mock.FALLBACK_SUMMARY = FALLBACK
    mock.OLLAMA_MODEL = MODEL_NAME
    mock.summarise_clinical_history.return_value = "AI: pneumonia, on IV ceftriaxone, obs stable."
    monkeypatch.setattr(ai_module, "ollama_client", mock)
    return mock


@pytest.fixture
def as_user(monkeypatch):
    """Setter that pins auth.CURRENT_USER. Tests call as_user(DOCTOR) etc."""
    def _set(user):
        monkeypatch.setattr(auth, "CURRENT_USER", dict(user))
        return user

    return _set


@pytest.fixture
def client(monkeypatch, db_mock, ollama_mock):
    """
    Flask test client with only the ai_summary blueprint mounted (its own
    AuthError + DatabaseClientError handlers come with it). db_mock / ollama_mock
    are pulled in so the patching happens before the app is built.
    """
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(ai_module.ai_summary_bp, url_prefix="/api/ai")
    app.config.update(TESTING=True)
    return app.test_client()


# ==========================================================================
# 1. Doctor, 'clinical' scope: summary generated and logged.
# ==========================================================================
def test_doctor_clinical_summary_generated_and_logged(client, db_mock, ollama_mock, as_user):
    """
    Logged in as the assigned doctor (id 1), POST /summarise-admission with
    summary_scope 'clinical':
      * the route feeds the admission's clinical-record text to ollama_client
        with scope 'clinical';
      * it logs one ai_summaries row - admission_id / patient_id / model_used /
        summary_scope='clinical' / review_status='pending';
      * the response carries the summary text and the new summary_id.
    """
    as_user(DOCTOR)
    db_mock.list_clinical_records.return_value = [_clinical_record()]

    resp = client.post(
        "/api/ai/summarise-admission",
        json={"admission_id": ADMISSION_ID, "summary_scope": "clinical"},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summary"] == "AI: pneumonia, on IV ceftriaxone, obs stable."
    assert body["summary_id"] == 1
    assert body["summary_scope"] == "clinical"
    assert body["ai_unavailable"] is False

    # The model was asked to summarise with the 'clinical' scope.
    text_arg, scope_arg = ollama_mock.summarise_clinical_history.call_args.args
    assert scope_arg == "clinical"
    assert "pneumonia" in text_arg.lower()

    # Exactly one ai_summaries row was written, with the expected columns.
    db_mock.create_ai_summary.assert_called_once()
    logged = db_mock.create_ai_summary.call_args.args[0]
    assert logged["admission_id"] == ADMISSION_ID
    assert logged["patient_id"] == PATIENT_ID
    assert logged["model_used"] == MODEL_NAME
    assert logged["summary_scope"] == "clinical"
    assert logged["review_status"] == "pending"
    assert logged["summary_text"] == "AI: pneumonia, on IV ceftriaxone, obs stable."


# ==========================================================================
# 2. Nurse, 'care_tasks' scope: summary generated and logged.
# ==========================================================================
def test_nurse_care_tasks_summary_generated_and_logged(client, db_mock, ollama_mock, as_user):
    """
    Logged in as the assigned nurse (id 7), POST /summarise-admission with scope
    'care_tasks':
      * clinical records are still read (only to resolve which records belong to
        the admission and the patient_id) but the text sent to the model is
        built from THIS nurse's care tasks, not the clinical notes;
      * one ai_summaries row is logged with summary_scope='care_tasks'.
    """
    as_user(NURSE)
    db_mock.list_clinical_records.return_value = [_clinical_record()]
    db_mock.list_care_tasks.return_value = [_care_task(nurse_id=NURSE["id"])]
    ollama_mock.summarise_clinical_history.return_value = "AI: IV antibiotics given, obs ongoing."

    resp = client.post(
        "/api/ai/summarise-admission",
        json={"admission_id": ADMISSION_ID, "summary_scope": "care_tasks"},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summary"] == "AI: IV antibiotics given, obs ongoing."
    assert body["summary_scope"] == "care_tasks"

    # Text came from the care task, and the diagnosis text was NOT included.
    text_arg, scope_arg = ollama_mock.summarise_clinical_history.call_args.args
    assert scope_arg == "care_tasks"
    assert "ceftriaxone" in text_arg.lower()          # from the task description/notes
    assert "Community-acquired pneumonia" not in text_arg  # clinical notes withheld

    logged = db_mock.create_ai_summary.call_args.args[0]
    assert logged["summary_scope"] == "care_tasks"
    assert logged["review_status"] == "pending"


# ==========================================================================
# 3. Nurse asking for 'clinical' / 'consultation' is rejected, not downgraded.
# ==========================================================================
@pytest.mark.parametrize("bad_scope", ["clinical", "consultation"])
def test_nurse_cannot_request_clinical_or_consultation_scope(
    client, db_mock, ollama_mock, as_user, bad_scope
):
    """
    A nurse must never reach clinical-record or consultation content. Asking for
    either scope returns 403, the model is never called, and nothing is logged
    to ai_summaries (no silent downgrade to 'care_tasks').
    """
    as_user(NURSE)
    db_mock.list_clinical_records.return_value = [_clinical_record()]
    db_mock.list_care_tasks.return_value = [_care_task(nurse_id=NURSE["id"])]

    resp = client.post(
        "/api/ai/summarise-admission",
        json={"admission_id": ADMISSION_ID, "summary_scope": bad_scope},
    )

    assert resp.status_code == 403
    assert bad_scope in resp.get_json()["error"]
    ollama_mock.summarise_clinical_history.assert_not_called()
    db_mock.create_ai_summary.assert_not_called()


# ==========================================================================
# 4. Admission with nothing to summarise -> a clear error, no broken row.
# ==========================================================================
@pytest.mark.parametrize(
    "actor, scope",
    [(DOCTOR, "clinical"), (NURSE, "care_tasks")],
    ids=["doctor-no-records", "nurse-no-tasks"],
)
def test_admission_with_no_content_returns_clear_error(
    client, db_mock, ollama_mock, as_user, actor, scope
):
    """
    When the admission has no clinical records (doctor) or no care tasks for
    this nurse, the route must fail with a clear 403/4xx message rather than
    calling the model with empty text or writing an empty ai_summaries row.
    The gather helpers raise AuthError -> handled as a JSON error with a message.
    """
    as_user(actor)
    # No rows for this admission in any table.
    db_mock.list_clinical_records.return_value = []
    db_mock.list_care_tasks.return_value = []

    resp = client.post(
        "/api/ai/summarise-admission",
        json={"admission_id": ADMISSION_ID, "summary_scope": scope},
    )

    assert resp.status_code == 403
    body = resp.get_json()
    assert "error" in body and body["error"]          # a human-readable message
    ollama_mock.summarise_clinical_history.assert_not_called()
    db_mock.create_ai_summary.assert_not_called()


# ==========================================================================
# 5. Ollama fallback value is still logged as an attempt.
# ==========================================================================
def test_ollama_fallback_still_logs_a_row(client, db_mock, ollama_mock, as_user):
    """
    ollama_client returns its FALLBACK_SUMMARY (LLM timed out / unavailable).
    The route must NOT skip logging: an ai_summaries row is still created, with
    summary_text set to the fallback string and review_status 'pending'. The
    response is 200 with ai_unavailable=True so the workflow keeps working.
    """
    as_user(DOCTOR)
    db_mock.list_clinical_records.return_value = [_clinical_record()]
    ollama_mock.summarise_clinical_history.return_value = FALLBACK

    resp = client.post(
        "/api/ai/summarise-admission",
        json={"admission_id": ADMISSION_ID, "summary_scope": "clinical"},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ai_unavailable"] is True
    assert body["summary"] == FALLBACK

    # The attempt was logged despite the failure.
    db_mock.create_ai_summary.assert_called_once()
    logged = db_mock.create_ai_summary.call_args.args[0]
    assert logged["summary_text"] == FALLBACK
    assert logged["review_status"] == "pending"
    assert logged["summary_scope"] == "clinical"


# ==========================================================================
# 6. Review route records 'accepted' / 'edited' / 'rejected'.
# ==========================================================================
@pytest.mark.parametrize("decision", ["accepted", "edited", "rejected"])
def test_review_records_each_valid_decision(client, db_mock, as_user, decision):
    """
    The original requester (doctor id 1, per source_reference) PUTs a decision.
    Each of the three valid values must yield 200 and translate to exactly
    db.update_ai_summary(id, {"review_status": <decision>,
                              "reviewed_by_staff_id": <caller id>}).
    """
    as_user(DOCTOR)
    db_mock.get_ai_summary.return_value = _summary_row(summary_id=1)
    db_mock.update_ai_summary.return_value = _summary_row(
        summary_id=1, review_status=decision, reviewed_by_staff_id=DOCTOR["id"]
    )

    resp = client.put("/api/ai/summaries/1/review", json={"review_status": decision})

    assert resp.status_code == 200
    assert resp.get_json()["ai_summary"]["review_status"] == decision
    db_mock.update_ai_summary.assert_called_once_with(
        1, {"review_status": decision, "reviewed_by_staff_id": DOCTOR["id"]}
    )


# ==========================================================================
# 7. Review route refuses to set review_status back to 'pending'.
# ==========================================================================
def test_review_rejects_setting_status_back_to_pending(client, db_mock, as_user):
    """
    'pending' is the un-reviewed state; a review can never restore it. The route
    must return 4xx and must not call db.update_ai_summary at all.
    """
    as_user(DOCTOR)
    db_mock.get_ai_summary.return_value = _summary_row(summary_id=1)

    resp = client.put("/api/ai/summaries/1/review", json={"review_status": "pending"})

    assert resp.status_code == 400
    assert "pending" in resp.get_json()["error"]
    db_mock.update_ai_summary.assert_not_called()


# ==========================================================================
# 8. Review route rejects an unrelated staff member.
# ==========================================================================
def test_review_rejects_unrelated_staff_member(client, db_mock, as_user):
    """
    The summary was requested by doctor 1 and its admission's record is assigned
    to doctor 1. Doctor 2 - not the requester, no record/consult/task on this
    admission - must be refused with 403 and no write must happen.
    """
    as_user(OTHER_DOCTOR)
    db_mock.get_ai_summary.return_value = _summary_row(
        summary_id=1, source_reference="requested_by_staff_id={}".format(DOCTOR["id"])
    )
    # Doctor 2 has no assignment relationship to this admission.
    db_mock.list_clinical_records.return_value = [_clinical_record(doctor_id=DOCTOR["id"])]
    db_mock.list_consultation_requests.return_value = []
    db_mock.list_care_tasks.return_value = []

    resp = client.put("/api/ai/summaries/1/review", json={"review_status": "accepted"})

    assert resp.status_code == 403
    assert "error" in resp.get_json()
    db_mock.update_ai_summary.assert_not_called()


# ==========================================================================
# 9. Review route freezes the AI-produced fields.
# ==========================================================================
def test_review_does_not_touch_frozen_ai_fields(client, db_mock, as_user):
    """
    A review must only ever write review_status + reviewed_by_staff_id. This
    asserts the update payload the route sends contains ONLY those two keys -
    summary_text, model_used, admission_id, patient_id, generated_at and
    summary_scope are never in it - even if the caller tries to smuggle them in
    the request body. It also checks the row echoed back still shows the
    original AI content.
    """
    as_user(DOCTOR)
    original = _summary_row(
        summary_id=1,
        scope="clinical",
        summary_text="ORIGINAL AI TEXT",
    )
    db_mock.get_ai_summary.return_value = original
    db_mock.update_ai_summary.return_value = {
        **original,
        "review_status": "edited",
        "reviewed_by_staff_id": DOCTOR["id"],
    }

    resp = client.put(
        "/api/ai/summaries/1/review",
        json={
            "review_status": "edited",
            # All of these must be ignored by the route:
            "summary_text": "TAMPERED",
            "model_used": "evil-model",
            "admission_id": 999,
            "patient_id": 999,
            "generated_at": "2000-01-01T00:00:00Z",
            "summary_scope": "care_tasks",
        },
    )

    assert resp.status_code == 200

    # The route sent ONLY the two permitted keys to the database.
    _sid, changes = db_mock.update_ai_summary.call_args.args
    assert set(changes.keys()) == {"review_status", "reviewed_by_staff_id"}
    assert "summary_text" not in changes
    assert "model_used" not in changes
    assert "admission_id" not in changes
    assert "summary_scope" not in changes

    # And the AI-produced content in the returned row is unchanged.
    returned = resp.get_json()["ai_summary"]
    assert returned["summary_text"] == "ORIGINAL AI TEXT"
    assert returned["model_used"] == MODEL_NAME
    assert returned["admission_id"] == ADMISSION_ID
    assert returned["summary_scope"] == "clinical"
