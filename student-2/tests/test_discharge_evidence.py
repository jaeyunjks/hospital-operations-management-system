# ruff: noqa: S101
"""
test_discharge_evidence.py - behaviour tests for
backend/services/discharge_evidence.py

services.database_client is replaced by a MagicMock on admission_data, the only
module the evidence builder reads through, so there is no socket to the DB
container and every database call can be inspected.

Scenarios covered:
  1. A clean admission: counts 0, all checks true, objective current 3.
  2. Open items flip exactly the right check and the objective's current.
  3. Only the open statuses count (completed / cancelled do not).
  4. Another admission's rows never count.
  5. note_status is the records' status word, or 'mixed'.
  6. No clinical records raises NoClinicalRecordsError; DB errors propagate.
  7. The admission id is an int at the dotted path the server's history uses.
  8. The evidence fits the Multi-Agent Server contract rules.
  9. No free text from the records enters the evidence; reads only.
"""

import json
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

from services import admission_data, discharge_evidence  # noqa: E402

ADMISSION_ID = 40
OTHER_ADMISSION_ID = 41
PATIENT_ID = 55
RECORD_ID = 10
OTHER_RECORD_ID = 11


# ------------------------------------------------------------
# Row builders - shaped like the database API returns them, with free text
# that must never reach the evidence.
# ------------------------------------------------------------
def _record(record_id=RECORD_ID, admission_id=ADMISSION_ID, status="open"):
    return {
        "record_id": record_id,
        "patient_id": PATIENT_ID,
        "admission_id": admission_id,
        "doctor_id": 1,
        "assessment_notes": "SECRET-NOTE productive cough",
        "diagnosis_summary": "SECRET-DIAGNOSIS pneumonia",
        "care_plan": "SECRET-PLAN IV ceftriaxone",
        "status": status,
    }


def _task(status="pending", record_id=RECORD_ID):
    return {
        "task_id": 20,
        "clinical_record_id": record_id,
        "assigned_nurse_id": 7,
        "task_description": "SECRET-TASK administer IV",
        "notes": "SECRET-NURSE-NOTE given 08:00",
        "status": status,
    }


def _consultation(status="requested", admission_id=ADMISSION_ID):
    return {
        "request_id": 30,
        "admission_id": admission_id,
        "specialist_id": 3,
        "reason_for_request": "SECRET-REASON chest review",
        "status": status,
    }


def _surgery(status="scheduled", admission_id=ADMISSION_ID):
    return {
        "request_id": 50,
        "admission_id": admission_id,
        "procedure_type": "SECRET-PROCEDURE appendectomy",
        "scheduled_at": "2026-09-05T09:00:00Z",
        "status": status,
    }


SECRETS = ("SECRET-",)


@pytest.fixture
def db_mock(monkeypatch):
    """Replace the database_client reference admission_data holds with a MagicMock."""
    mock = MagicMock(name="database_client")
    mock.DatabaseClientError = admission_data.db.DatabaseClientError
    mock.list_clinical_records.return_value = [_record()]
    mock.list_consultation_requests.return_value = []
    mock.list_care_tasks.return_value = []
    mock.list_surgery_requests.return_value = []
    monkeypatch.setattr(admission_data, "db", mock)
    return mock


def _build(admission_id=ADMISSION_ID):
    return discharge_evidence.build_discharge_evidence(admission_id)


# ==========================================================================
# 1. A clean admission.
# ==========================================================================
def test_clean_admission_is_ready(db_mock):
    task, evidence = _build()

    assert task == "Discharge readiness review: admission 40"
    assert evidence["open_care_tasks"] == 0
    assert evidence["pending_consultations"] == 0
    assert evidence["scheduled_surgeries"] == 0
    assert evidence["objective"] == {
        "metric": "readiness checks met",
        "current": 3,
        "target": 3,
        "unit": "check",
    }
    assert evidence["options"][0]["attributes"] == {
        "no_open_care_tasks": True,
        "no_pending_consultations": True,
        "no_scheduled_surgery": True,
    }
    assert evidence["current_state"]["patient_id"] == PATIENT_ID


# ==========================================================================
# 2. Open items flip exactly the right check.
# ==========================================================================
@pytest.mark.parametrize(
    "rows, count_key, check",
    [
        ({"list_care_tasks": [_task("pending")]}, "open_care_tasks", "no_open_care_tasks"),
        (
            {"list_consultation_requests": [_consultation("requested")]},
            "pending_consultations",
            "no_pending_consultations",
        ),
        (
            {"list_surgery_requests": [_surgery("scheduled")]},
            "scheduled_surgeries",
            "no_scheduled_surgery",
        ),
    ],
    ids=["care-task", "consultation", "surgery"],
)
def test_one_open_item_fails_only_its_check(db_mock, rows, count_key, check):
    for method, value in rows.items():
        getattr(db_mock, method).return_value = value

    _task_text, evidence = _build()

    assert evidence[count_key] == 1
    attributes = evidence["options"][0]["attributes"]
    assert attributes[check] is False
    assert [name for name, ok in attributes.items() if ok] == [
        name for name in attributes if name != check
    ]
    assert evidence["objective"]["current"] == 2
    assert evidence["options"][0]["outcomes"] == {"metric_delta": 1, "projected_value": 3}


def test_everything_open_gives_zero_checks_met(db_mock):
    db_mock.list_care_tasks.return_value = [_task("pending"), _task("acknowledged")]
    db_mock.list_consultation_requests.return_value = [
        _consultation("requested"),
        _consultation("in_review"),
    ]
    db_mock.list_surgery_requests.return_value = [_surgery("scheduled")]

    _task_text, evidence = _build()

    assert evidence["open_care_tasks"] == 2
    assert evidence["pending_consultations"] == 2
    assert evidence["scheduled_surgeries"] == 1
    assert evidence["objective"]["current"] == 0
    assert not any(evidence["options"][0]["attributes"].values())


# ==========================================================================
# 3. Only the open statuses count.
# ==========================================================================
@pytest.mark.parametrize(
    "status, counted",
    [("pending", 1), ("acknowledged", 1), ("completed", 0), ("cancelled", 0)],
)
def test_care_task_open_statuses(db_mock, status, counted):
    db_mock.list_care_tasks.return_value = [_task(status)]
    assert _build()[1]["open_care_tasks"] == counted


@pytest.mark.parametrize(
    "status, counted",
    [("requested", 1), ("in_review", 1), ("completed", 0), ("cancelled", 0)],
)
def test_consultation_pending_statuses(db_mock, status, counted):
    db_mock.list_consultation_requests.return_value = [_consultation(status)]
    assert _build()[1]["pending_consultations"] == counted


@pytest.mark.parametrize(
    "status, counted", [("scheduled", 1), ("completed", 0), ("cancelled", 0)]
)
def test_surgery_counts_status_not_date(db_mock, status, counted):
    # A future scheduled_at is irrelevant; only the status word matters.
    db_mock.list_surgery_requests.return_value = [_surgery(status)]
    assert _build()[1]["scheduled_surgeries"] == counted


# ==========================================================================
# 4. Another admission's rows never count.
# ==========================================================================
def test_other_admissions_rows_are_ignored(db_mock):
    db_mock.list_clinical_records.return_value = [
        _record(),
        _record(record_id=OTHER_RECORD_ID, admission_id=OTHER_ADMISSION_ID),
    ]
    # This task hangs off the OTHER admission's record.
    db_mock.list_care_tasks.return_value = [_task("pending", record_id=OTHER_RECORD_ID)]
    db_mock.list_consultation_requests.return_value = [
        _consultation("requested", admission_id=OTHER_ADMISSION_ID)
    ]
    db_mock.list_surgery_requests.return_value = [
        _surgery("scheduled", admission_id=OTHER_ADMISSION_ID)
    ]

    _task_text, evidence = _build()

    assert evidence["open_care_tasks"] == 0
    assert evidence["pending_consultations"] == 0
    assert evidence["scheduled_surgeries"] == 0
    assert evidence["objective"]["current"] == 3


# ==========================================================================
# 5. note_status.
# ==========================================================================
@pytest.mark.parametrize("status", ["open", "under_review", "closed", "archived"])
def test_note_status_is_the_records_status_word(db_mock, status):
    db_mock.list_clinical_records.return_value = [_record(status=status)]
    assert _build()[1]["note_status"] == status


def test_note_status_same_across_records(db_mock):
    db_mock.list_clinical_records.return_value = [
        _record(record_id=10, status="closed"),
        _record(record_id=12, status="closed"),
    ]
    assert _build()[1]["note_status"] == "closed"


def test_note_status_mixed_when_records_differ(db_mock):
    db_mock.list_clinical_records.return_value = [
        _record(record_id=10, status="open"),
        _record(record_id=12, status="closed"),
    ]
    assert _build()[1]["note_status"] == "mixed"


def test_note_status_is_not_a_rule(db_mock):
    # An 'open' record does not change the checks, the objective or constraints.
    db_mock.list_clinical_records.return_value = [_record(status="open")]
    _task_text, evidence = _build()
    assert evidence["objective"]["current"] == 3
    assert all(c.get("field") != "note_status" for c in evidence["constraints"])


# ==========================================================================
# 6. Errors.
# ==========================================================================
def test_no_clinical_records_raises_custom_error(db_mock):
    db_mock.list_clinical_records.return_value = []

    with pytest.raises(discharge_evidence.NoClinicalRecordsError) as excinfo:
        _build()

    assert excinfo.value.admission_id == ADMISSION_ID
    assert "no clinical records" in str(excinfo.value)
    db_mock.list_care_tasks.assert_not_called()


def test_only_other_admissions_records_counts_as_no_records(db_mock):
    db_mock.list_clinical_records.return_value = [_record(admission_id=OTHER_ADMISSION_ID)]
    with pytest.raises(discharge_evidence.NoClinicalRecordsError):
        _build()


@pytest.mark.parametrize(
    "failing",
    [
        "list_clinical_records",
        "list_care_tasks",
        "list_consultation_requests",
        "list_surgery_requests",
    ],
)
def test_database_errors_propagate(db_mock, failing):
    error = admission_data.db.DatabaseClientError("database down")
    getattr(db_mock, failing).side_effect = error

    with pytest.raises(admission_data.db.DatabaseClientError):
        _build()


@pytest.mark.parametrize("bad_id", ["abc", None, ""])
def test_non_integer_admission_id_is_rejected(db_mock, bad_id):
    with pytest.raises(ValueError):
        _build(bad_id)
    db_mock.list_clinical_records.assert_not_called()


# ==========================================================================
# 7. Identity for the server's history search.
# ==========================================================================
def test_admission_id_is_an_int_at_the_history_path(db_mock):
    task, evidence = _build("40")  # a string id from a URL is coerced

    admission_id = evidence["current_state"]["admission"]["admission_id"]
    assert admission_id == ADMISSION_ID
    assert type(admission_id) is int
    assert task == "Discharge readiness review: admission 40"
    assert task.startswith(discharge_evidence.TASK_PREFIX)
    assert evidence["evidence_type"] == discharge_evidence.EVIDENCE_TYPE


def test_constants():
    assert discharge_evidence.TASK_PREFIX == "Discharge readiness review: "
    assert discharge_evidence.EVIDENCE_TYPE == "student2_discharge_readiness"


# ==========================================================================
# 8. Multi-Agent Server contract rules.
# ==========================================================================
def test_top_level_keys_are_exactly_the_agreed_set(db_mock):
    _task_text, evidence = _build()
    assert set(evidence) == {
        "schema_version",
        "evidence_type",
        "scenario",
        "current_state",
        "open_care_tasks",
        "pending_consultations",
        "scheduled_surgeries",
        "note_status",
        "objective",
        "options",
        "constraints",
        "unknowns",
    }


def test_single_option_with_label_equal_to_id_and_no_risks(db_mock):
    _task_text, evidence = _build()

    assert len(evidence["options"]) == 1
    option = evidence["options"][0]
    assert option["id"] == "ready_to_discharge"
    assert option["label"] == option["id"]
    assert option["risk_indicators"] == []


def test_each_boolean_has_exactly_one_matching_hard_constraint(db_mock):
    _task_text, evidence = _build()

    attributes = evidence["options"][0]["attributes"]
    assert all(isinstance(value, bool) for value in attributes.values())
    hard = [c for c in evidence["constraints"] if c["scope"] == "option"]
    assert sorted(c["id"] for c in hard) == sorted(attributes)
    for constraint in hard:
        assert constraint["type"] == "hard"
        assert constraint["operator"] == "equals"
        assert constraint["value"] is True
        assert constraint["field"] == constraint["id"]
        assert constraint["field"] in attributes


def test_one_workflow_approval_constraint_and_no_policy_text(db_mock):
    _task_text, evidence = _build()

    workflow = [c for c in evidence["constraints"] if c["scope"] == "workflow"]
    assert workflow == [
        {
            "id": "human_approval",
            "scope": "workflow",
            "type": "approval",
            "description": "The assigned doctor decides; AI is advisory only.",
        }
    ]
    assert "preferences" not in evidence
    assert {c["type"] for c in evidence["constraints"]} == {"hard", "approval"}


@pytest.mark.parametrize("open_count", [0, 1, 2, 3])
def test_objective_rises_toward_target_and_gap_is_never_negative(db_mock, open_count):
    db_mock.list_care_tasks.return_value = [_task("pending")] if open_count > 0 else []
    db_mock.list_consultation_requests.return_value = (
        [_consultation("requested")] if open_count > 1 else []
    )
    db_mock.list_surgery_requests.return_value = (
        [_surgery("scheduled")] if open_count > 2 else []
    )

    _task_text, evidence = _build()

    objective = evidence["objective"]
    assert objective["current"] == 3 - open_count
    assert 0 <= objective["current"] <= objective["target"] == 3
    assert objective["target"] - objective["current"] >= 0
    assert isinstance(objective["current"], int)


def test_unknowns_are_a_short_list_of_strings(db_mock):
    _task_text, evidence = _build()
    assert 1 <= len(evidence["unknowns"]) <= 5
    assert all(isinstance(item, str) for item in evidence["unknowns"])


# ==========================================================================
# 9. No free text, reads only.
# ==========================================================================
def test_no_free_text_from_the_records_enters_the_evidence(db_mock):
    db_mock.list_care_tasks.return_value = [_task("pending")]
    db_mock.list_consultation_requests.return_value = [_consultation("requested")]
    db_mock.list_surgery_requests.return_value = [_surgery("scheduled")]

    task, evidence = _build()

    serialised = json.dumps(evidence) + task
    for secret in SECRETS:
        assert secret not in serialised
    assert "appendectomy" not in serialised
    assert "2026-09-05" not in serialised  # surgery dates are not sent


def test_only_list_reads_are_made(db_mock):
    db_mock.list_care_tasks.return_value = [_task("pending")]

    _build()

    called = {call[0] for call in db_mock.method_calls}
    assert called <= {
        "list_clinical_records",
        "list_care_tasks",
        "list_consultation_requests",
        "list_surgery_requests",
    }
