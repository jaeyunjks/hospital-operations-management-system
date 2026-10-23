"""
test_multi_agent_routes.py - behaviour tests for backend/routes/multi_agent_routes.py

Same pattern as test_mcp_routes.py: the blueprint runs in a real Flask test
client and the real services/multi_agent_client.py talks over localhost to the
FakeMultiAgentServer from helpers/fake_ai_servers.py, started on a random free
port for each test. No real server, no real network.

The database API is never contacted either. FakeDatabase below replaces the
few services.database_client functions the route reaches (the list_* reads
used by admission_data and create_ai_summary), so every saved ai_summaries row
can be inspected. Its clinical records mirror the seed data the task describes:
Dr Chen (id 1) is assigned to admissions 1 to 6 and 8, Dr Nair (id 2) to 7.

Most tests set up their workflow directly on the fake (create_workflow +
advance), which is not recorded as a request, so "the fake saw zero requests"
and "exactly one review" are exact counts.

Who is calling is set per test via the `as_user` fixture (auth.CURRENT_USER);
the default caller is Dr Chen.

Scenarios covered:
  1. POST /start: success, bad admission_id, wrong role, wrong doctor, switch
     off, server down / slow / failing.
  2. GET /workflows/<id>: in progress, awaiting review, failed, bad id, a
     workflow on another doctor's admission, a wrong-prefix workflow, and the
     reply being an allowlist.
  3. POST /workflows/<id>/review: all four decisions and the row each saves,
     correct without a comment, a second review (409, no second row), and a
     save that fails after the server accepted the review.
  4. GET /latest: right admission, "none", never a wrong-prefix workflow.
"""

import os
import sys
import uuid

import pytest

# --- Make backend/ and tests/helpers importable ----------------------------
TESTS_DIR = os.path.abspath(os.path.dirname(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(TESTS_DIR, os.pardir, "backend"))
for _path in (BACKEND_DIR, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import auth  # noqa: E402
from routes import multi_agent_routes as ma_module  # noqa: E402
from services import database_client, discharge_evidence, multi_agent_client  # noqa: E402
from helpers.fake_ai_servers import (  # noqa: E402
    AWAITING_HUMAN_REVIEW,
    FAILED,
    FAILED_WORKFLOW,
    IN_PROGRESS,
    MA_DECISION_STATUS,
    MA_MODELS,
    NOT_READY,
    READY,
    SERVER_ERROR,
    SLOW,
    WRONG_PREFIX_TASK,
    fake_multi_agent_server,  # noqa: F401  (pytest fixture, used by name below)
)

DR_CHEN = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}
DR_NAIR = {"id": 2, "name": "Dr Priya Nair", "role": "doctor"}
SPECIALIST = {"id": 3, "name": "Dr Emily Brown", "role": "specialist"}
NURSE = {"id": 7, "name": "James Wilson", "role": "nurse"}
RECEPTIONIST = {"id": 99, "name": "Pat Adams", "role": "receptionist"}

CHEN_ADMISSION = 1
OTHER_CHEN_ADMISSION = 2
NAIR_ADMISSION = 7
UNKNOWN_ADMISSION = 99   # no clinical records, so no assigned doctor

START_URL = "/api/multi-agent/start"
LATEST_URL = "/api/multi-agent/latest"
WORKFLOW_URL = "/api/multi-agent/workflows/{}"
REVIEW_URL = "/api/multi-agent/workflows/{}/review"
SOME_UUID = "11111111-2222-3333-4444-555555555555"

VIEW_KEYS = {
    "workflow_id", "status", "admission_id", "stages", "outcome",
    "checks", "gap", "recommendation", "decision",
}
ROW_KEYS = {
    "admission_id", "patient_id", "summary_text", "model_used", "summary_scope",
    "review_status", "reviewed_by_staff_id", "source_reference", "source_documents",
}
REVIEW_STATUS_FOR = {
    "approve": "accepted",
    "correct": "edited",
    "reject": "rejected",
    "partially_accept": "partially_accepted",
}


# ------------------------------------------------------------
# A stand-in for the database API
# ------------------------------------------------------------
class FakeDatabase:
    """In-memory rows for the database_client functions the route reaches."""

    def __init__(self):
        # Admission n has one record (id n*10, patient 100+n); Dr Nair owns 7.
        self.clinical_records = [
            {
                "record_id": n * 10,
                "patient_id": 100 + n,
                "admission_id": n,
                "doctor_id": 2 if n == NAIR_ADMISSION else 1,
                "status": "open",
            }
            for n in range(1, 9)
        ]
        self.care_tasks = []
        self.consultations = []
        self.surgeries = []
        self.summaries = []     # every ai_summaries row the route created
        self.fail_save = False  # True: create_ai_summary raises a database error

    def create_ai_summary(self, payload):
        if self.fail_save:
            raise database_client.DatabaseClientError(
                "database at student-2-db:5000 refused the write", status_code=500
            )
        row = dict(payload, summary_id=len(self.summaries) + 1)
        self.summaries.append(row)
        return row


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------
@pytest.fixture
def as_user(monkeypatch):
    """Setter that pins auth.CURRENT_USER. Tests call as_user(DR_NAIR) etc."""
    def _set(user):
        monkeypatch.setattr(auth, "CURRENT_USER", dict(user))
        return user

    return _set


@pytest.fixture
def fake_db(monkeypatch):
    """Point database_client's reads and its one write at FakeDatabase."""
    db = FakeDatabase()
    monkeypatch.setattr(database_client, "list_clinical_records", lambda: list(db.clinical_records))
    monkeypatch.setattr(database_client, "list_care_tasks", lambda: list(db.care_tasks))
    monkeypatch.setattr(database_client, "list_consultation_requests", lambda: list(db.consultations))
    monkeypatch.setattr(database_client, "list_surgery_requests", lambda: list(db.surgeries))
    monkeypatch.setattr(database_client, "create_ai_summary", db.create_ai_summary)
    return db


@pytest.fixture
def client(monkeypatch, fake_multi_agent_server, fake_db):
    """
    Flask test client with only the multi-agent blueprint mounted. The fake
    server fixture runs first, so the switch is ON and the address points at
    the fake. The caller starts as Dr Chen.
    """
    from flask import Flask

    monkeypatch.setenv("MULTI_AGENT_TIMEOUT", "5")  # a dead server can never hang a test
    monkeypatch.setattr(auth, "CURRENT_USER", dict(DR_CHEN))

    app = Flask(__name__)
    app.register_blueprint(ma_module.multi_agent_bp, url_prefix="/api/multi-agent")
    app.config.update(TESTING=True)
    return app.test_client()


# ------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------
NO_BODY = object()  # marker: send the POST with no body at all


def _post(client, url, body):
    if body is NO_BODY:
        return client.post(url)
    return client.post(url, json=body)


def _assert_error_shape(resp, status, code):
    """Every error reply is {"status": ..., "error": {"code", "message"}}."""
    body = resp.get_json()
    assert body["status"] == status
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body


def _workflow_on(fake, admission_id=CHEN_ADMISSION, advance=True, **advance_options):
    """A workflow for this admission, built from the real evidence builder."""
    task, evidence = discharge_evidence.build_discharge_evidence(admission_id)
    workflow_id = fake.create_workflow(task, evidence)
    if advance:
        fake.advance(workflow_id, **advance_options)
    return workflow_id


def _review(client, workflow_id, decision="approve", comment="Checked with the ward"):
    return client.post(
        REVIEW_URL.format(workflow_id), json={"decision": decision, "comment": comment}
    )


def _only_row(fake_db):
    assert len(fake_db.summaries) == 1
    return fake_db.summaries[0]


# Every doctor-only route, as (method, url, body) - for the role and switch tests.
DOCTOR_ONLY_ROUTES = [
    pytest.param("POST", START_URL, {"admission_id": CHEN_ADMISSION}, id="start"),
    pytest.param("GET", WORKFLOW_URL.format(SOME_UUID), None, id="progress"),
    pytest.param("POST", REVIEW_URL.format(SOME_UUID), {"decision": "approve", "comment": ""}, id="review"),
    pytest.param("GET", LATEST_URL + "?admission_id=1", None, id="latest"),
]


# ==========================================================================
# 1. POST /start
# ==========================================================================
def test_start_success_returns_202_and_an_in_progress_view(client, fake_multi_agent_server, fake_db):
    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 202
    body = resp.get_json()
    assert body["status"] == "ok"
    workflow = body["workflow"]
    uuid.UUID(workflow["workflow_id"])  # a real UUID
    assert workflow["status"] == IN_PROGRESS
    assert workflow["admission_id"] == CHEN_ADMISSION
    assert [(s["name"], s["done"], s["source"]) for s in workflow["stages"]] == [
        ("planner", False, None), ("worker", False, None), ("reviewer", False, None),
    ]
    assert workflow["outcome"] is None
    assert workflow["recommendation"] is None
    assert workflow["decision"] is None
    assert fake_db.summaries == []   # starting a review saves nothing


def test_start_sends_one_discharge_submission_for_that_admission(client, fake_multi_agent_server):
    client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert [(r.method, r.path) for r in fake_multi_agent_server.requests] == [("POST", "/workflows")]
    submission = fake_multi_agent_server.submissions[0]
    assert submission["task"] == "Discharge readiness review: admission 1"
    assert submission["evidence"]["current_state"]["admission"]["admission_id"] == CHEN_ADMISSION


def test_start_view_shows_the_checks_and_gap_from_our_evidence(client, fake_db):
    fake_db.care_tasks = [{"clinical_record_id": 10, "status": "pending"}]  # one open task on admission 1

    workflow = client.post(START_URL, json={"admission_id": CHEN_ADMISSION}).get_json()["workflow"]

    assert {c["id"]: c["passed"] for c in workflow["checks"]} == {
        "no_open_care_tasks": False,
        "no_pending_consultations": True,
        "no_scheduled_surgery": True,
    }
    assert workflow["gap"] == {"current": 2, "target": 3}


BAD_START_BODIES = [
    pytest.param(NO_BODY, id="no_body"),
    pytest.param([1, 2, 3], id="body_is_a_list"),
    pytest.param({}, id="missing_admission_id"),
    pytest.param({"admission_id": 0}, id="zero"),
    pytest.param({"admission_id": -5}, id="negative"),
    pytest.param({"admission_id": "1"}, id="number_as_text"),
    pytest.param({"admission_id": True}, id="boolean"),
    pytest.param({"admission_id": 1.5}, id="decimal"),
    pytest.param({"admission_id": None}, id="null"),
    pytest.param({"admission_id": 1, "extra": "x"}, id="extra_field"),
]


@pytest.mark.parametrize("body", BAD_START_BODIES)
def test_start_bad_input_is_400_and_reaches_nothing(client, fake_multi_agent_server, body):
    resp = _post(client, START_URL, body)

    assert resp.status_code == 400
    _assert_error_shape(resp, "error", "invalid_request")
    assert fake_multi_agent_server.request_count == 0


@pytest.mark.parametrize("user", [NURSE, SPECIALIST, RECEPTIONIST], ids=lambda u: u["role"])
@pytest.mark.parametrize("method, url, body", DOCTOR_ONLY_ROUTES)
def test_non_doctor_roles_get_403_on_every_route(
    client, fake_multi_agent_server, fake_db, as_user, user, method, url, body
):
    as_user(user)

    resp = client.open(url, method=method, json=body)

    assert resp.status_code == 403
    _assert_error_shape(resp, "error", "forbidden")
    assert fake_multi_agent_server.request_count == 0
    assert fake_db.summaries == []


@pytest.mark.parametrize("switch", ["true", "false"])
def test_blocked_role_reply_does_not_depend_on_the_switch(
    client, fake_multi_agent_server, as_user, monkeypatch, switch
):
    as_user(NURSE)
    monkeypatch.setenv("MULTI_AGENT_ENABLED", switch)

    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 403
    _assert_error_shape(resp, "error", "forbidden")


def test_start_on_another_doctors_admission_is_403(client, fake_multi_agent_server):
    resp = client.post(START_URL, json={"admission_id": NAIR_ADMISSION})  # Dr Chen calling

    assert resp.status_code == 403
    _assert_error_shape(resp, "error", "forbidden")
    assert fake_multi_agent_server.request_count == 0


def test_the_other_doctor_cannot_start_on_dr_chens_admission(client, fake_multi_agent_server, as_user):
    as_user(DR_NAIR)

    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 403
    assert fake_multi_agent_server.request_count == 0


def test_start_on_an_admission_with_no_records_is_403(client, fake_multi_agent_server):
    resp = client.post(START_URL, json={"admission_id": UNKNOWN_ADMISSION})

    assert resp.status_code == 403
    _assert_error_shape(resp, "error", "forbidden")
    assert fake_multi_agent_server.request_count == 0


@pytest.mark.parametrize("method, url, body", DOCTOR_ONLY_ROUTES)
def test_switch_off_is_503_disabled_and_reaches_nothing(
    client, fake_multi_agent_server, monkeypatch, method, url, body
):
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "false")

    resp = client.open(url, method=method, json=body)

    assert resp.status_code == 503
    assert resp.get_json() == {
        "status": "disabled",
        "error": {"code": "multi_agent_disabled", "message": "Multi-Agent review is not enabled."},
    }
    assert fake_multi_agent_server.request_count == 0


def test_stopped_server_is_503_unavailable_with_no_address(client, fake_multi_agent_server):
    port = fake_multi_agent_server.port  # read before stop() clears it
    fake_multi_agent_server.stop()

    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 503
    body = _assert_error_shape(resp, "unavailable", "multi_agent_unavailable")
    assert body["error"]["message"] == "The Multi-Agent Server is temporarily unavailable."
    text = resp.get_data(as_text=True)
    assert str(port) not in text
    assert "Traceback" not in text


def test_slow_server_is_504_timeout(client, fake_multi_agent_server, monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_TIMEOUT", "0.3")
    fake_multi_agent_server.set_mode(SLOW, delay=2)

    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 504
    _assert_error_shape(resp, "unavailable", "multi_agent_timeout")


def test_server_error_text_is_not_leaked(client, fake_multi_agent_server):
    fake_multi_agent_server.set_mode(SERVER_ERROR, status=503)

    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 503
    assert "Workflow execution" not in resp.get_data(as_text=True)


def test_start_never_returns_a_wrong_prefix_workflow(client, fake_multi_agent_server):
    """The server stored a foreign task: the route must refuse to present it."""
    fake_multi_agent_server.set_mode("wrong_prefix")

    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 502
    _assert_error_shape(resp, "error", "multi_agent_invalid_response")
    assert "workflow" not in resp.get_json()


@pytest.mark.parametrize("variant", ["missing_fields", "not_json"])
def test_start_with_a_bad_server_reply_is_503_unavailable(client, fake_multi_agent_server, variant):
    fake_multi_agent_server.set_mode("bad_reply", variant=variant)

    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 503
    _assert_error_shape(resp, "unavailable", "multi_agent_unavailable")
    assert "not json" not in resp.get_data(as_text=True)


# ==========================================================================
# 2. GET /workflows/<id>
# ==========================================================================
def test_progress_in_progress_workflow(client, fake_multi_agent_server):
    workflow_id = _workflow_on(fake_multi_agent_server, advance=False)

    resp = client.get(WORKFLOW_URL.format(workflow_id))

    assert resp.status_code == 200
    workflow = resp.get_json()["workflow"]
    assert workflow["workflow_id"] == workflow_id
    assert workflow["status"] == IN_PROGRESS
    assert [s["done"] for s in workflow["stages"]] == [False, False, False]
    assert workflow["outcome"] is None
    assert workflow["recommendation"] is None
    assert workflow["decision"] is None


def test_progress_awaiting_review_ready(client, fake_multi_agent_server):
    workflow_id = _workflow_on(fake_multi_agent_server, outcome=READY)

    workflow = client.get(WORKFLOW_URL.format(workflow_id)).get_json()["workflow"]

    assert workflow["status"] == AWAITING_HUMAN_REVIEW
    assert [(s["name"], s["done"], s["source"]) for s in workflow["stages"]] == [
        ("planner", True, MA_MODELS["planner"]),
        ("worker", True, MA_MODELS["worker"]),
        ("reviewer", True, MA_MODELS["reviewer"]),
    ]
    assert workflow["outcome"] == "ready"
    assert all(c["passed"] for c in workflow["checks"])
    assert [c["id"] for c in workflow["checks"]] == [cid for cid, _ in discharge_evidence.CHECKS]
    assert workflow["gap"] == {"current": 3, "target": 3}
    assert isinstance(workflow["recommendation"]["text"], str) and workflow["recommendation"]["text"]
    assert workflow["recommendation"]["risks"] == list(discharge_evidence.UNKNOWNS)
    assert workflow["decision"] is None


def test_progress_awaiting_review_not_ready(client, fake_multi_agent_server, fake_db):
    fake_db.care_tasks = [{"clinical_record_id": 10, "status": "pending"}]
    workflow_id = _workflow_on(
        fake_multi_agent_server, outcome=NOT_READY, failed_checks=("no_open_care_tasks",)
    )

    workflow = client.get(WORKFLOW_URL.format(workflow_id)).get_json()["workflow"]

    assert workflow["status"] == AWAITING_HUMAN_REVIEW
    assert workflow["outcome"] == "not_ready"
    assert workflow["gap"] == {"current": 2, "target": 3}
    failed = [c["id"] for c in workflow["checks"] if not c["passed"]]
    assert failed == ["no_open_care_tasks"]


def test_progress_rule_based_stage_is_labelled_rule_based(client, fake_multi_agent_server):
    workflow_id = _workflow_on(fake_multi_agent_server)
    record = fake_multi_agent_server._workflows[workflow_id]   # a stage the server answered with no live model
    record["reviewer"]["execution"].update(provider="deterministic_fallback", model=None, fallback_used=True)

    workflow = client.get(WORKFLOW_URL.format(workflow_id)).get_json()["workflow"]

    assert workflow["stages"][2] == {"name": "reviewer", "done": True, "source": "rule-based"}


def test_progress_failed_workflow_has_no_recommendation_and_no_verdict(client, fake_multi_agent_server):
    fake_multi_agent_server.set_mode(FAILED_WORKFLOW)
    workflow_id = _workflow_on(fake_multi_agent_server)

    workflow = client.get(WORKFLOW_URL.format(workflow_id)).get_json()["workflow"]

    assert workflow["status"] == FAILED
    assert [s["done"] for s in workflow["stages"]] == [True, True, False]
    assert workflow["recommendation"] is None
    assert workflow["decision"] is None
    assert workflow["outcome"] is None   # a run the Reviewer never validated must not show a verdict


def test_progress_failed_workflow_error_text_is_not_leaked(client, fake_multi_agent_server):
    fake_multi_agent_server.set_mode(FAILED_WORKFLOW)
    workflow_id = _workflow_on(fake_multi_agent_server)

    text = client.get(WORKFLOW_URL.format(workflow_id)).get_data(as_text=True)

    assert "model response was empty" not in text


def test_progress_bad_workflow_id_is_404_and_reaches_nothing(client, fake_multi_agent_server):
    resp = client.get(WORKFLOW_URL.format("not-a-uuid"))

    assert resp.status_code == 404
    _assert_error_shape(resp, "error", "workflow_not_found")
    assert fake_multi_agent_server.request_count == 0


def test_progress_unknown_workflow_is_404(client):
    resp = client.get(WORKFLOW_URL.format(SOME_UUID))

    assert resp.status_code == 404
    _assert_error_shape(resp, "error", "workflow_not_found")


def test_progress_on_another_doctors_admission_is_404_and_shows_nothing(client, fake_multi_agent_server):
    workflow_id = _workflow_on(fake_multi_agent_server, NAIR_ADMISSION)

    resp = client.get(WORKFLOW_URL.format(workflow_id))

    assert resp.status_code == 404
    _assert_error_shape(resp, "error", "workflow_not_found")
    assert "workflow" not in resp.get_json()


def test_progress_of_a_wrong_prefix_workflow_is_404(client, fake_multi_agent_server):
    _, evidence = discharge_evidence.build_discharge_evidence(CHEN_ADMISSION)
    workflow_id = fake_multi_agent_server.create_workflow(WRONG_PREFIX_TASK, evidence)
    fake_multi_agent_server.advance(workflow_id)

    resp = client.get(WORKFLOW_URL.format(workflow_id))

    assert resp.status_code == 404
    _assert_error_shape(resp, "error", "workflow_not_found")


def test_progress_reply_is_an_allowlist(client, fake_multi_agent_server):
    workflow_id = _workflow_on(fake_multi_agent_server)

    resp = client.get(WORKFLOW_URL.format(workflow_id))

    body = resp.get_json()
    assert set(body) == {"status", "workflow"}
    assert set(body["workflow"]) == VIEW_KEYS
    assert all(set(stage) == {"name", "done", "source"} for stage in body["workflow"]["stages"])
    assert set(body["workflow"]["recommendation"]) == {"text", "risks"}
    text = resp.get_data(as_text=True)
    for hidden in ("submitted_evidence", "Discharge readiness review: ", "ready_to_discharge",
                   "plan_steps_followed", "mutation_executed", '"open_care_tasks"'):
        assert hidden not in text


# ==========================================================================
# 3. POST /workflows/<id>/review
# ==========================================================================
@pytest.mark.parametrize("decision", list(REVIEW_STATUS_FOR))
def test_each_decision_is_recorded_and_returned(client, fake_multi_agent_server, decision):
    workflow_id = _workflow_on(fake_multi_agent_server)

    resp = _review(client, workflow_id, decision, "Checked with the ward")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["logged"] is True
    assert body["workflow"]["status"] == MA_DECISION_STATUS[decision]
    assert body["workflow"]["decision"] == {"decision": decision, "comment": "Checked with the ward"}
    assert fake_multi_agent_server.reviews == [
        {"workflow_id": workflow_id, "body": {"decision": decision, "comment": "Checked with the ward"}}
    ]


@pytest.mark.parametrize("decision, expected_status", list(REVIEW_STATUS_FOR.items()))
def test_each_decision_saves_one_final_row_with_the_right_status(
    client, fake_multi_agent_server, fake_db, decision, expected_status
):
    workflow_id = _workflow_on(fake_multi_agent_server)

    _review(client, workflow_id, decision)

    row = _only_row(fake_db)
    assert row["review_status"] == expected_status
    assert row["summary_scope"] == "discharge_readiness"
    assert row["reviewed_by_staff_id"] == DR_CHEN["id"]
    assert row["source_reference"] == "requested_by_staff_id=1"
    assert row["admission_id"] == CHEN_ADMISSION
    assert row["patient_id"] == 100 + CHEN_ADMISSION
    assert row["model_used"] == MA_MODELS["reviewer"]
    assert row["source_documents"] == []


def test_saved_row_has_exactly_the_agreed_columns(client, fake_multi_agent_server, fake_db):
    workflow_id = _workflow_on(fake_multi_agent_server)

    _review(client, workflow_id)

    assert set(_only_row(fake_db)) - {"summary_id"} == ROW_KEYS


def test_workflow_id_is_the_last_line_and_not_in_source_reference(client, fake_multi_agent_server, fake_db):
    workflow_id = _workflow_on(fake_multi_agent_server)

    _review(client, workflow_id)

    row = _only_row(fake_db)
    assert row["summary_text"].split("\n")[-1] == "Workflow: " + workflow_id
    assert workflow_id not in row["source_reference"]


def test_saved_text_holds_result_checks_note_decision_and_comment(client, fake_multi_agent_server, fake_db):
    workflow_id = _workflow_on(fake_multi_agent_server, outcome=READY)

    _review(client, workflow_id, "partially_accept", "Discharge after physio")

    lines = _only_row(fake_db)["summary_text"].split("\n")
    assert "Result: Ready" in lines
    assert "Checks met: 3 of 3" in lines
    assert "- No open care tasks: passed" in lines
    assert any(line.startswith("Reviewer note: ") and line != "Reviewer note: none" for line in lines)
    assert "Doctor decision: partially_accept" in lines
    assert "Doctor comment: Discharge after physio" in lines


def test_saved_text_says_not_ready_when_a_check_failed(client, fake_multi_agent_server, fake_db):
    fake_db.care_tasks = [{"clinical_record_id": 10, "status": "pending"}]
    workflow_id = _workflow_on(
        fake_multi_agent_server, outcome=NOT_READY, failed_checks=("no_open_care_tasks",)
    )

    _review(client, workflow_id, "reject", "Task still open")

    lines = _only_row(fake_db)["summary_text"].split("\n")
    assert "Result: Not ready" in lines
    assert "Checks met: 2 of 3" in lines
    assert "- No open care tasks: not passed" in lines


def test_a_comment_cannot_fake_the_workflow_line(client, fake_multi_agent_server, fake_db):
    workflow_id = _workflow_on(fake_multi_agent_server)

    _review(client, workflow_id, "approve", "fine\nWorkflow: 99999999-9999-9999-9999-999999999999")

    lines = _only_row(fake_db)["summary_text"].split("\n")
    assert lines[-1] == "Workflow: " + workflow_id
    assert lines.count("Workflow: 99999999-9999-9999-9999-999999999999") == 0


def test_review_only_talks_to_the_workflow_api(client, fake_multi_agent_server):
    workflow_id = _workflow_on(fake_multi_agent_server)

    _review(client, workflow_id)

    paths = {r.path.partition("?")[0] for r in fake_multi_agent_server.requests}
    assert paths == {"/workflows/" + workflow_id, "/workflows/{}/review".format(workflow_id)}


@pytest.mark.parametrize("comment", [pytest.param(NO_BODY, id="omitted"), "", "   "])
def test_correct_without_a_comment_is_400_and_changes_nothing(
    client, fake_multi_agent_server, fake_db, comment
):
    workflow_id = _workflow_on(fake_multi_agent_server)
    body = {"decision": "correct"} if comment is NO_BODY else {"decision": "correct", "comment": comment}

    resp = client.post(REVIEW_URL.format(workflow_id), json=body)

    assert resp.status_code == 400
    error = _assert_error_shape(resp, "error", "invalid_request")["error"]
    assert "comment" in error["message"]
    assert fake_multi_agent_server.reviews == []
    assert fake_db.summaries == []
    assert fake_multi_agent_server.workflow(workflow_id)["final_status"] == AWAITING_HUMAN_REVIEW


@pytest.mark.parametrize("decision", ["approve", "reject", "partially_accept"])
def test_other_decisions_accept_a_blank_comment(client, fake_multi_agent_server, fake_db, decision):
    workflow_id = _workflow_on(fake_multi_agent_server)

    resp = client.post(REVIEW_URL.format(workflow_id), json={"decision": decision})

    assert resp.status_code == 200
    assert resp.get_json()["workflow"]["decision"] == {"decision": decision, "comment": ""}
    assert len(fake_db.summaries) == 1


def test_comment_of_1001_characters_is_400(client, fake_multi_agent_server, fake_db):
    workflow_id = _workflow_on(fake_multi_agent_server)

    resp = _review(client, workflow_id, "approve", "x" * 1001)

    assert resp.status_code == 400
    _assert_error_shape(resp, "error", "invalid_request")
    assert fake_multi_agent_server.reviews == []
    assert fake_db.summaries == []


def test_comment_of_exactly_1000_characters_is_accepted(client, fake_multi_agent_server, fake_db):
    workflow_id = _workflow_on(fake_multi_agent_server)

    resp = _review(client, workflow_id, "approve", "x" * 1000)

    assert resp.status_code == 200
    assert "Doctor comment: " + "x" * 1000 in _only_row(fake_db)["summary_text"]


BAD_REVIEW_BODIES = [
    pytest.param(NO_BODY, id="no_body"),
    pytest.param(["approve"], id="body_is_a_list"),
    pytest.param({}, id="missing_decision"),
    pytest.param({"decision": "discharge"}, id="unknown_decision"),
    pytest.param({"decision": "APPROVE"}, id="wrong_case"),
    pytest.param({"decision": 5}, id="decision_not_text"),
    pytest.param({"decision": "approve", "comment": 5}, id="comment_not_text"),
    pytest.param({"decision": "approve", "extra": "x"}, id="extra_field"),
]


@pytest.mark.parametrize("body", BAD_REVIEW_BODIES)
def test_review_bad_body_is_400_and_reaches_nothing(client, fake_multi_agent_server, fake_db, body):
    resp = _post(client, REVIEW_URL.format(SOME_UUID), body)

    assert resp.status_code == 400
    _assert_error_shape(resp, "error", "invalid_request")
    assert fake_multi_agent_server.request_count == 0
    assert fake_db.summaries == []


def test_review_of_a_bad_workflow_id_is_404_and_reaches_nothing(client, fake_multi_agent_server):
    resp = _review(client, "not-a-uuid")

    assert resp.status_code == 404
    _assert_error_shape(resp, "error", "workflow_not_found")
    assert fake_multi_agent_server.request_count == 0


def test_review_on_another_doctors_admission_is_404_and_changes_nothing(
    client, fake_multi_agent_server, fake_db
):
    workflow_id = _workflow_on(fake_multi_agent_server, NAIR_ADMISSION)

    resp = _review(client, workflow_id)

    assert resp.status_code == 404
    _assert_error_shape(resp, "error", "workflow_not_found")
    assert fake_multi_agent_server.reviews == []
    assert fake_db.summaries == []
    assert fake_multi_agent_server.workflow(workflow_id)["final_status"] == AWAITING_HUMAN_REVIEW


def test_review_of_a_wrong_prefix_workflow_is_404_and_changes_nothing(
    client, fake_multi_agent_server, fake_db
):
    _, evidence = discharge_evidence.build_discharge_evidence(CHEN_ADMISSION)
    workflow_id = fake_multi_agent_server.create_workflow(WRONG_PREFIX_TASK, evidence)
    fake_multi_agent_server.advance(workflow_id)

    resp = _review(client, workflow_id)

    assert resp.status_code == 404
    assert fake_multi_agent_server.reviews == []
    assert fake_db.summaries == []


def test_review_of_a_workflow_still_in_progress_is_409(client, fake_multi_agent_server, fake_db):
    workflow_id = _workflow_on(fake_multi_agent_server, advance=False)

    resp = _review(client, workflow_id)

    assert resp.status_code == 409
    _assert_error_shape(resp, "error", "invalid_state")
    assert fake_multi_agent_server.reviews == []
    assert fake_db.summaries == []


def test_review_of_a_failed_workflow_is_409(client, fake_multi_agent_server, fake_db):
    fake_multi_agent_server.set_mode(FAILED_WORKFLOW)
    workflow_id = _workflow_on(fake_multi_agent_server)

    resp = _review(client, workflow_id)

    assert resp.status_code == 409
    assert fake_db.summaries == []


def test_second_review_is_409_and_makes_no_second_row(client, fake_multi_agent_server, fake_db):
    workflow_id = _workflow_on(fake_multi_agent_server)
    first = _review(client, workflow_id, "approve", "first")

    second = _review(client, workflow_id, "reject", "second")

    assert first.status_code == 200
    assert second.status_code == 409
    _assert_error_shape(second, "error", "invalid_state")
    assert len(fake_db.summaries) == 1
    assert fake_db.summaries[0]["review_status"] == "accepted"
    assert len(fake_multi_agent_server.reviews) == 1


def test_a_review_the_server_refuses_with_409_makes_no_row(
    client, fake_multi_agent_server, fake_db, monkeypatch
):
    """
    Two clicks racing: the route's first read still says "awaiting", but the
    other click's decision lands before this one is sent. The server says 409.
    """
    workflow_id = _workflow_on(fake_multi_agent_server)
    real_get = multi_agent_client.get

    def get_then_lose_the_race(wid):
        result = real_get(wid)
        fake_multi_agent_server.decide(wid, "approve", "the other tab")
        return result

    monkeypatch.setattr(multi_agent_client, "get", get_then_lose_the_race)

    resp = _review(client, workflow_id, "reject", "this tab")

    assert resp.status_code == 409
    _assert_error_shape(resp, "error", "invalid_state")
    assert fake_db.summaries == []
    assert fake_multi_agent_server.workflow(workflow_id)["human_review"]["comments"] == "the other tab"


def test_save_failure_after_the_server_accepted_is_still_a_success_with_logged_false(
    client, fake_multi_agent_server, fake_db
):
    fake_db.fail_save = True
    workflow_id = _workflow_on(fake_multi_agent_server)

    resp = _review(client, workflow_id, "approve", "ok")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["logged"] is False
    assert body["workflow"]["decision"] == {"decision": "approve", "comment": "ok"}
    assert fake_multi_agent_server.workflow(workflow_id)["final_status"] == "approved"
    assert fake_db.summaries == []


def test_save_failure_never_calls_the_server_again(client, fake_multi_agent_server, fake_db):
    fake_db.fail_save = True
    workflow_id = _workflow_on(fake_multi_agent_server)

    _review(client, workflow_id)

    assert len(fake_multi_agent_server.reviews) == 1
    # the route's read, the client's read before writing, the one review POST - nothing after it
    assert [r.method for r in fake_multi_agent_server.requests] == ["GET", "GET", "POST"]


def test_save_failure_text_is_not_leaked(client, fake_multi_agent_server, fake_db):
    fake_db.fail_save = True
    workflow_id = _workflow_on(fake_multi_agent_server)

    text = _review(client, workflow_id).get_data(as_text=True)

    assert "student-2-db" not in text
    assert "refused" not in text


def test_database_fault_while_checking_the_doctor_is_a_fixed_502(client, fake_multi_agent_server, monkeypatch):
    def broken_read():
        raise database_client.DatabaseClientError("db at student-2-db:5000 down", status_code=500)

    monkeypatch.setattr(database_client, "list_clinical_records", broken_read)

    resp = client.post(START_URL, json={"admission_id": CHEN_ADMISSION})

    assert resp.status_code == 502
    _assert_error_shape(resp, "error", "database_unavailable")
    assert "student-2-db" not in resp.get_data(as_text=True)
    assert fake_multi_agent_server.request_count == 0


# ==========================================================================
# 4. GET /latest?admission_id=N
# ==========================================================================
def test_latest_finds_the_newest_review_of_the_right_admission(client, fake_multi_agent_server):
    task1, evidence1 = discharge_evidence.build_discharge_evidence(CHEN_ADMISSION)
    task2, evidence2 = discharge_evidence.build_discharge_evidence(OTHER_CHEN_ADMISSION)
    fake_multi_agent_server.create_workflow(task1, evidence1, started_at="2026-01-01T00:00:00.000Z")
    newest = fake_multi_agent_server.create_workflow(task1, evidence1, started_at="2026-02-01T00:00:00.000Z")
    fake_multi_agent_server.create_workflow(task2, evidence2, started_at="2026-03-01T00:00:00.000Z")

    resp = client.get(LATEST_URL + "?admission_id=1")

    assert resp.status_code == 200
    workflow = resp.get_json()["workflow"]
    assert workflow["workflow_id"] == newest
    assert workflow["admission_id"] == CHEN_ADMISSION


def test_latest_shows_the_decision_once_made(client, fake_multi_agent_server):
    workflow_id = _workflow_on(fake_multi_agent_server)
    fake_multi_agent_server.decide(workflow_id, "reject", "Not yet")

    workflow = client.get(LATEST_URL + "?admission_id=1").get_json()["workflow"]

    assert workflow["status"] == "rejected"
    assert workflow["decision"] == {"decision": "reject", "comment": "Not yet"}


def test_latest_is_none_when_nothing_was_started(client):
    resp = client.get(LATEST_URL + "?admission_id=1")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "none"}


def test_latest_is_none_when_only_another_admission_has_a_review(client, fake_multi_agent_server):
    _workflow_on(fake_multi_agent_server, OTHER_CHEN_ADMISSION)

    resp = client.get(LATEST_URL + "?admission_id=1")

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "none"}


def test_latest_never_returns_a_wrong_prefix_workflow(client, fake_multi_agent_server):
    _, evidence = discharge_evidence.build_discharge_evidence(CHEN_ADMISSION)
    wrong = fake_multi_agent_server.create_workflow(WRONG_PREFIX_TASK, evidence)

    resp = client.get(LATEST_URL + "?admission_id=1")

    assert resp.status_code != 200
    assert wrong not in resp.get_data(as_text=True)
    assert "workflow" not in resp.get_json()


def test_latest_on_another_doctors_admission_is_403(client, fake_multi_agent_server):
    resp = client.get(LATEST_URL + "?admission_id=7")

    assert resp.status_code == 403
    _assert_error_shape(resp, "error", "forbidden")
    assert fake_multi_agent_server.request_count == 0


@pytest.mark.parametrize("query", ["", "?admission_id=", "?admission_id=abc", "?admission_id=0",
                                   "?admission_id=-1", "?admission_id=1.5", "?admission_id=%EF%BC%91"])
def test_latest_bad_admission_id_is_400_and_reaches_nothing(client, fake_multi_agent_server, query):
    resp = client.get(LATEST_URL + query)

    assert resp.status_code == 400
    _assert_error_shape(resp, "error", "invalid_request")
    assert fake_multi_agent_server.request_count == 0
