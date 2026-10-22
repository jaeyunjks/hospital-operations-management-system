"""Room & Bed -> shared Multi-Agent Server (Release 2).

No Multi-Agent Server is started: ``_request`` in the client is the seam,
replaced by a small fake that behaves like ai-services/multi-agent-server
(202 on create, in_progress -> awaiting_human_review, 409 when reviewed
twice). What matters is that the case evidence fits the shared contract,
that a person must review before anything happens, that a workflow from
another feature is refused, and that a disabled or missing server never
breaks the API.
"""

import importlib
import uuid

import pytest

from conftest import data, error

WORKFLOW_ID = "6f1c1d2a-0c55-4c39-9d8e-3b8c7f0a1b2c"


class FakeServer:
    """Just enough of the shared server's HTTP contract."""

    def __init__(self, evidence_type="student-4-bed-shortage"):
        self.calls = []
        self.record = None
        self.evidence_type = evidence_type

    def view(self, include_evidence=False):
        view = {key: self.record[key] for key in (
            "schema_version", "workflow_id", "status", "task", "planner", "worker",
            "reviewer", "human_review", "final_result")}
        if include_evidence:
            view["submitted_evidence"] = self.record["evidence"]
        return view

    def finish_agents(self, chosen):
        options = self.record["evidence"]["options"]
        label = next(o["label"] for o in options if o["id"] == chosen)  # equals the id
        stage = {"started_at": "2026-10-21T01:00:00.000Z", "completed_at": "2026-10-21T01:00:05.000Z",
                 "execution": {"provider": "live_ollama", "model": "llama3.1:8b"}}
        self.record.update({
            "status": "awaiting_human_review",
            "planner": {**stage, "output": {"goal": "Place the patient", "planned_steps": ["Compare beds"],
                                            "required_evidence": ["objective", "options"]}},
            "worker": {**stage, "output": {"status": "proposal_ready", "proposed_solution": {
                "selected_option_id": chosen, "selected_option": label, "reasoning": ["Free now"]}}},
            "reviewer": {**stage, "output": {"review_status": "validated_with_risks", "risks": [],
                                             "recommended_option_id": chosen,
                                             "recommendation": "Review before action."}},
        })

    def __call__(self, method, path, payload=None, query=None):
        self.calls.append((method, path, payload, query))
        if path == "/health":
            return 200, {"status": "ok", "service": "multi-agent-server"}
        if method == "POST" and path == "/workflows":
            evidence = dict(payload["evidence"])
            evidence["evidence_type"] = self.evidence_type
            self.record = {"schema_version": "1.0", "workflow_id": WORKFLOW_ID, "status": "in_progress",
                           "task": payload["task"], "planner": None, "worker": None, "reviewer": None,
                           "human_review": None, "final_result": None, "evidence": evidence}
            return 202, self.view()
        if method == "GET" and path == "/workflows":
            return 200, {"schema_version": "1.0", "count": 1, "workflows": [
                {"workflow_id": WORKFLOW_ID, "status": self.record["status"],
                 "started_at": "2026-10-21T01:00:00.000Z", "human_review": None}]}
        if path == "/workflows/" + WORKFLOW_ID and method == "GET":
            if self.record is None:
                return 404, {"error": {"code": "not_found", "message": "Workflow not found"}}
            return 200, self.view(include_evidence=bool(query))
        if path == "/workflows/{}/review".format(WORKFLOW_ID):
            if self.record["status"] != "awaiting_human_review":
                return 409, {"error": {"code": "invalid_workflow_state",
                                       "message": "workflow is not awaiting Human Review"}}
            status = {"approve": "approved", "reject": "rejected", "correct": "corrected",
                      "partially_accept": "partially_accepted"}[payload["decision"]]
            self.record.update(status=status, human_review={
                "decision": payload["decision"], "comments": payload["comment"],
                "timestamp": "2026-10-21T01:01:00.000Z"})
            return 200, self.view()
        return 404, {}


@pytest.fixture()
def mac(monkeypatch):
    module = importlib.import_module("services.multi_agent_client")
    importlib.reload(module)
    monkeypatch.setattr(module, "MULTI_AGENT_ENABLED", True)
    return module


@pytest.fixture()
def server(mac, monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(mac, "_request", fake)
    return fake


def open_case(api):
    return data(api.get("/api/shortage-cases?status=Open"))[0]


# ----------------------------------------------------------------- evidence


def test_evidence_matches_the_shared_contract(api):
    case = open_case(api)
    body = data(api.get("/api/shortage-cases/{}/multi-agent/evidence".format(case["case_id"])))
    evidence = body["evidence"]

    assert body["task"].startswith("Recommend how to place the patient")
    assert evidence["evidence_type"] == "student-4-bed-shortage"
    assert evidence["case_id"] == case["case_id"]
    assert {"objective", "options", "constraints", "unknowns"} <= set(evidence)
    assert "patient_id" not in str(evidence)

    ids = [o["id"] for o in evidence["options"]]
    assert ids and all(i.startswith("bed-") for i in ids) and len(ids) == len(set(ids))
    for option in evidence["options"]:
        assert {"id", "label", "attributes", "outcomes", "risk_indicators"} <= set(option)
        assert option["label"] == option["id"] and option["risk_indicators"] == []
        assert option["outcomes"]["metric_delta"] == 1

    assert {c["type"] for c in evidence["constraints"]} == {"hard", "approval", "safety"}
    hard_fields = {c["field"] for c in evidence["constraints"] if c["type"] == "hard"}
    for option in evidence["options"]:
        booleans = {k for k, v in option["attributes"].items() if isinstance(v, bool)}
        assert booleans == hard_fields, "every boolean attribute must be exactly one hard constraint"
    hard = [c for c in evidence["constraints"] if c["type"] == "hard"]
    assert all(c["scope"] == "option" and c["operator"] == "equals" for c in hard)
    assert any(c["type"] == "approval" for c in evidence["constraints"])


def test_case_facts_are_top_level_keys(api):
    """The shared Planner may only cite top-level keys; nested case facts made
    llama3.1:8b cite keys like 'urgency' that did not exist at the top level."""
    case = open_case(api)
    evidence = data(api.get("/api/shortage-cases/{}/multi-agent/evidence".format(case["case_id"])))["evidence"]
    for key in ("case_id", "status", "urgency", "required_care_category", "required_ward",
                "holding_location", "compatible_beds_found", "bed_options_included"):
        assert key in evidence
    assert not any(isinstance(v, dict) for k, v in evidence.items()
                   if k not in ("objective", "provenance"))


def test_evidence_uses_the_same_options_as_the_screen(api):
    case = open_case(api)
    screen = data(api.get("/api/shortage-cases/{}/options".format(case["case_id"])))["options"]
    evidence = data(api.get("/api/shortage-cases/{}/multi-agent/evidence".format(case["case_id"])))
    bed_ids = [o["bed_id"] for o in screen if o["bed_id"]][:3]
    assert [o["id"] for o in evidence["evidence"]["options"]] == ["bed-{}".format(b) for b in bed_ids]


def test_bed_ids_map_back_from_option_ids():
    module = importlib.import_module("services.multi_agent_evidence")
    assert module.bed_id_from_option("bed-14") == 14
    assert module.bed_id_from_option("escalate") is None
    assert module.bed_id_from_option("bed-x") is None


# ----------------------------------------------------------------- workflow


def test_disabled_by_default(api):
    module = importlib.import_module("services.multi_agent_client")
    importlib.reload(module)
    assert data(api.get("/api/multi-agent/status")) == {
        "enabled": False, "server_url": "http://127.0.0.1:8200", "reachable": False}
    case = open_case(api)
    response = api.post("/api/shortage-cases/{}/multi-agent".format(case["case_id"]))
    assert response.status_code == 503 and "disabled" in error(response)


def test_status_reports_a_reachable_server(api, server):
    assert data(api.get("/api/multi-agent/status"))["reachable"] is True


def test_full_workflow_needs_human_review_and_reserves_nothing(api, server):
    case = open_case(api)
    started = api.post("/api/shortage-cases/{}/multi-agent".format(case["case_id"]))
    assert started.status_code == 202
    assert data(started)["status"] == "in_progress"
    assert server.calls[-1][2]["evidence"]["evidence_type"] == "student-4-bed-shortage"

    first_bed = server.record["evidence"]["options"][0]["id"]
    server.finish_agents(first_bed)
    waiting = data(api.get("/api/multi-agent/workflows/" + WORKFLOW_ID))
    assert waiting["status"] == "awaiting_human_review"
    assert waiting["case_id"] == case["case_id"]
    assert waiting["recommendation"]["bed_id"] == int(first_bed[4:])
    assert waiting["recommendation"]["label"].startswith("Bed ")
    assert "submitted_evidence" not in waiting

    reviewed = data(api.post("/api/multi-agent/workflows/{}/review".format(WORKFLOW_ID),
                             json={"decision": "approve", "comment": "Ward agreed"}))
    assert reviewed["status"] == "approved"
    assert reviewed["human_review"]["comments"] == "Reviewer: dev.coordinator. Ward agreed"

    bed = data(api.get("/api/beds/{}".format(int(first_bed[4:]))))
    assert bed["status"] == "available"
    assert data(api.get("/api/shortage-cases/{}".format(case["case_id"])))["status"] == "Open"


def test_history_is_scoped_to_the_case(api, server):
    case = open_case(api)
    api.post("/api/shortage-cases/{}/multi-agent".format(case["case_id"]))
    history = data(api.get("/api/shortage-cases/{}/multi-agent".format(case["case_id"])))
    assert history[0]["workflow_id"] == WORKFLOW_ID
    query = server.calls[-1][3]
    assert query["evidence_type"] == "student-4-bed-shortage"
    assert (query["evidence_path"], query["evidence_value"]) == ("case_id", str(case["case_id"]))


@pytest.mark.parametrize("body, message", [
    ({"decision": "maybe"}, "decision must be one of"),
    ({"decision": "correct", "comment": "  "}, "correction must say"),
    ({"decision": "approve", "extra": 1}, "Only 'decision' and 'comment'"),
])
def test_review_input_is_validated(api, server, body, message):
    case = open_case(api)
    api.post("/api/shortage-cases/{}/multi-agent".format(case["case_id"]))
    server.finish_agents(server.record["evidence"]["options"][0]["id"])
    response = api.post("/api/multi-agent/workflows/{}/review".format(WORKFLOW_ID), json=body)
    assert response.status_code == 400 and message in error(response)


def test_review_before_the_agents_finish_is_a_conflict(api, server):
    case = open_case(api)
    api.post("/api/shortage-cases/{}/multi-agent".format(case["case_id"]))
    response = api.post("/api/multi-agent/workflows/{}/review".format(WORKFLOW_ID),
                        json={"decision": "approve"})
    assert response.status_code == 409


def test_workflow_from_another_feature_is_refused(api, mac, monkeypatch):
    fake = FakeServer(evidence_type="student-5-staffing-recovery")
    monkeypatch.setattr(mac, "_request", fake)
    case = open_case(api)
    api.post("/api/shortage-cases/{}/multi-agent".format(case["case_id"]))
    assert api.get("/api/multi-agent/workflows/" + WORKFLOW_ID).status_code == 404


def test_closed_case_cannot_start_a_workflow(api, server):
    closed = data(api.get("/api/shortage-cases?status=Resolved"))[0]
    response = api.post("/api/shortage-cases/{}/multi-agent".format(closed["case_id"]))
    assert response.status_code == 409 and server.calls == []


def test_invalid_workflow_id_never_reaches_the_server(api, server):
    response = api.get("/api/multi-agent/workflows/not-a-uuid")
    assert response.status_code == 400 and server.calls == []


def test_unreachable_server_is_a_clean_502(api, mac, monkeypatch):
    import urllib.error

    def down(*_args, **_kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(mac, "_request", down)
    case = open_case(api)
    response = api.post("/api/shortage-cases/{}/multi-agent".format(case["case_id"]))
    assert response.status_code == 502 and "unavailable" in error(response)
    assert data(api.get("/api/multi-agent/status"))["reachable"] is False


def test_non_http_server_url_is_refused(mac, monkeypatch):
    monkeypatch.setattr(mac, "MULTI_AGENT_SERVER_URL", "file:///etc/passwd")
    result = mac.get(str(uuid.uuid4()))
    assert not result.ok and result.outcome == "unavailable"


def test_case_without_beds_is_not_sent_to_the_agents(api, server, monkeypatch):
    evidence_module = importlib.import_module("services.multi_agent_evidence")
    monkeypatch.setattr(evidence_module.shortage_options, "options_for",
                        lambda case: [{"kind": "escalate", "bed_id": None, "note": "Escalate"}])
    case = open_case(api)
    response = api.post("/api/shortage-cases/{}/multi-agent".format(case["case_id"]))
    assert response.status_code == 409 and "escalate" in error(response)
    assert server.calls == []
