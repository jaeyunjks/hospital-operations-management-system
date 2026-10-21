"""Frontend Multi-Agent panel: renders each workflow stage from the backend view.

The backend is stubbed at the frontend's ``api`` helper, so this checks only
what the coordinator sees: polling while agents run, the Human Review form
when the workflow waits, and the reserve button only after approval.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
WORKFLOW_ID = "6f1c1d2a-0c55-4c39-9d8e-3b8c7f0a1b2c"


@pytest.fixture()
def frontend():
    sys.modules.pop("rb_frontend_ma", None)
    spec = importlib.util.spec_from_file_location("rb_frontend_ma", FRONTEND_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["rb_frontend_ma"] = module
    spec.loader.exec_module(module)
    module.app.config.update(TESTING=True)
    return module


def workflow(status, human=None):
    stage = {"started_at": "t", "completed_at": "t", "execution": {"provider": "live_ollama"}}
    done = status != "in_progress"
    return {
        "workflow_id": WORKFLOW_ID, "status": status, "case_id": 2,
        "planner": {**stage, "output": {"goal": "Place the patient", "planned_steps": ["Compare beds"],
                                        "required_evidence": ["options"]}},
        "worker": {**stage, "output": {"status": "proposal_ready", "proposed_solution": {
            "selected_option": "Bed GW1-03-A in room GW1-03, General Ward 1",
            "reasoning": ["Free now in the requested ward"]}}} if done else None,
        "reviewer": {**stage, "output": {"review_status": "validated_with_risks", "risks": ["None major"],
                                         "recommendation": "Review before action."}} if done else None,
        "human_review": human,
        "recommendation": {"option_id": "bed-9", "bed_id": 9, "kind": "available_now",
                           "label": "Bed GW1-03-A in room GW1-03, General Ward 1"} if done else None,
    }


def stub(module, monkeypatch, responses):
    calls = []

    def fake(method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return responses.get((method, path), (None, "not stubbed"))

    monkeypatch.setattr(module, "api", fake)
    return calls


def test_running_workflow_polls_itself(frontend, monkeypatch):
    stub(frontend, monkeypatch, {("POST", "/api/shortage-cases/2/multi-agent"): (workflow("in_progress"), None)})
    html = frontend.app.test_client().post("/shortages/2/multi-agent").get_data(as_text=True)
    assert 'hx-get="/multi-agent/{}"'.format(WORKFLOW_ID) in html
    assert "Place the patient" in html and "Evaluating the options" in html


def test_waiting_workflow_shows_the_human_review_form(frontend, monkeypatch):
    stub(frontend, monkeypatch, {("GET", "/api/multi-agent/workflows/" + WORKFLOW_ID):
                                 (workflow("awaiting_human_review"), None)})
    html = frontend.app.test_client().get("/multi-agent/" + WORKFLOW_ID).get_data(as_text=True)
    assert "Record review" in html and 'value="partially_accept"' in html
    assert "hx-trigger" not in html
    assert "Reserve" not in html


def test_review_is_forwarded_and_approval_offers_the_reserve_step(frontend, monkeypatch):
    approved = workflow("approved", human={"decision": "approve", "comments": "Reviewer: a. ok",
                                           "timestamp": "t"})
    calls = stub(frontend, monkeypatch, {
        ("POST", "/api/multi-agent/workflows/{}/review".format(WORKFLOW_ID)): (approved, None)})
    html = frontend.app.test_client().post("/multi-agent/{}/review".format(WORKFLOW_ID),
                                           data={"decision": "approve", "comment": "ok"}
                                           ).get_data(as_text=True)
    assert calls[0][2] == {"decision": "approve", "comment": "ok"}
    assert 'action="/shortages/2/decide"' in html and 'name="resolved_bed_id" value="9"' in html


def test_rejected_workflow_offers_no_action(frontend, monkeypatch):
    rejected = workflow("rejected", human={"decision": "reject", "comments": "No", "timestamp": "t"})
    stub(frontend, monkeypatch, {("GET", "/api/multi-agent/workflows/" + WORKFLOW_ID): (rejected, None)})
    html = frontend.app.test_client().get("/multi-agent/" + WORKFLOW_ID).get_data(as_text=True)
    assert "/decide" not in html and "reject" in html


def test_review_error_keeps_the_current_workflow_visible(frontend, monkeypatch):
    stub(frontend, monkeypatch, {
        ("POST", "/api/multi-agent/workflows/{}/review".format(WORKFLOW_ID)): (None, "A correction must say what to change"),
        ("GET", "/api/multi-agent/workflows/" + WORKFLOW_ID): (workflow("awaiting_human_review"), None)})
    html = frontend.app.test_client().post("/multi-agent/{}/review".format(WORKFLOW_ID),
                                           data={"decision": "correct"}).get_data(as_text=True)
    assert "A correction must say what to change" in html and "Record review" in html


@pytest.mark.parametrize("status, expected", [
    ({"enabled": False, "reachable": False}, "Multi-Agent disabled"),
    ({"enabled": True, "reachable": True}, "Run Multi-Agent review"),
])
def test_case_page_shows_the_panel_state(frontend, monkeypatch, status, expected):
    case = {"case_id": 2, "patient_id": 1017, "required_care_category": "Long-term", "required_ward": None,
            "urgency": "Medium", "holding_location": None, "opened_at": "t", "opened_by": "x",
            "status": "Open", "decision_reason": None}
    stub(frontend, monkeypatch, {
        ("GET", "/api/shortage-cases/2/options"): ({"case": case, "options": []}, None),
        ("GET", "/api/multi-agent/status"): (status, None),
        ("GET", "/api/shortage-cases/2/multi-agent"): ([{"workflow_id": WORKFLOW_ID, "status": "approved",
                                                       "started_at": "t"}], None)})
    html = frontend.app.test_client().get("/shortages/2").get_data(as_text=True)
    assert expected in html
    assert (WORKFLOW_ID[:8] in html) == status["enabled"]
