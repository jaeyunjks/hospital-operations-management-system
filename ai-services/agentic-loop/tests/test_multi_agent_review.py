from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))

import agentic_loop  # noqa: E402
import multi_agent_review as mar  # noqa: E402

EXAMPLE = json.loads((AGENT_DIR / "examples" / "multi-agent-workflow.example.json").read_text(encoding="utf-8"))
REVIEW = {"assessment": "Coordinated.", "recommendations": [{"priority": "low", "target": "MA4",
          "action": "Keep evidence ids stable."}], "next_action": "None.", "outcome": "pass"}


class ScriptedModel:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def checks_for(workflow):
    return {(c["id"]): c for c in mar.check_workflow(mar.observe(workflow, "test"))}


def write(tmp_path, workflow, name="wf.json"):
    path = tmp_path / name
    path.write_text(json.dumps(workflow), encoding="utf-8")
    return path


def test_example_workflow_passes_every_check():
    assert all(c["passed"] for c in checks_for(EXAMPLE).values())


def test_missing_human_review_fails_order_and_decision():
    workflow = copy.deepcopy(EXAMPLE)
    workflow["steps"] = workflow["steps"][:3]
    checks = checks_for(workflow)
    assert not checks["MA1"]["passed"] and "human" in checks["MA1"]["detail"]
    assert not checks["MA6"]["passed"]


def test_unreviewed_last_worker_output_fails_order():
    workflow = copy.deepcopy(EXAMPLE)
    worker = copy.deepcopy(workflow["steps"][1])
    workflow["steps"].insert(3, worker)
    assert "never reviewed" in checks_for(workflow)["MA1"]["detail"]


def test_human_review_alias_is_accepted():
    workflow = copy.deepcopy(EXAMPLE)
    workflow["steps"][3]["agent"] = "Human_Review"
    assert checks_for(workflow)["MA1"]["passed"]


def test_correction_without_comment_fails():
    workflow = copy.deepcopy(EXAMPLE)
    workflow["steps"][3]["output"] = {"decision": "corrected", "reviewer": "Asher", "comment": ""}
    check = checks_for(workflow)["MA6"]
    assert not check["passed"] and "correction details" in check["detail"]


def test_uncovered_evidence_is_only_a_warning():
    workflow = copy.deepcopy(EXAMPLE)
    workflow["steps"][1]["output"]["evidence_used"] = ["ward_occupancy:Emergency"]
    check = checks_for(workflow)["MA4"]
    assert not check["passed"] and check["severity"] == "advisory"
    assert mar.summarise_checks(list(checks_for(workflow).values()))["verdict"] == "pass"


def test_unordered_audit_log_and_missing_actor_fail():
    workflow = copy.deepcopy(EXAMPLE)
    workflow["audit_log"] = list(reversed(workflow["audit_log"]))[:-1]
    detail = checks_for(workflow)["MA7"]["detail"]
    assert "not in order" in detail and "planner" in detail


def test_review_runs_files_and_server_ids(tmp_path):
    class FakeClient:
        def workflow(self, workflow_id):
            return mar.unwrap({"success": True, "data": {**EXAMPLE, "workflow_id": workflow_id}})

    model = ScriptedModel(REVIEW)
    result = mar.MultiAgentReview(generate=model, files=[write(tmp_path, EXAMPLE)], workflow_ids=["wf-2"],
                                  client=FakeClient(), log=lambda _m: None).run()
    assert result["summary"]["verdict"] == "pass", result["summary"]
    assert [o["workflow_id"] for o in result["observations"]] == ["wf-20261021-0001", "wf-2"]
    assert "Planner" in model.prompts[0] and "wf-2" in model.prompts[0]


def test_review_needs_some_evidence():
    with pytest.raises(ValueError):
        mar.MultiAgentReview(generate=ScriptedModel())


def test_main_writes_evidence_and_dispatches(tmp_path, monkeypatch):
    monkeypatch.setattr(mar, "model_from_args", lambda _a: ScriptedModel(REVIEW))
    logs = tmp_path / "logs"
    code = agentic_loop.main(["--mode", "multi-agent", "--workflow", str(write(tmp_path, EXAMPLE)),
                              "--logs-dir", str(logs)])
    assert code == 0
    markdown = next(logs.glob("*-multi-agent-review.md")).read_text(encoding="utf-8")
    assert "Verdict: **PASS**" in markdown and "planner -> worker -> reviewer -> human" in markdown


def test_main_exit_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(mar, "model_from_args", lambda _a: ScriptedModel(RuntimeError("offline")))
    assert mar.main(["--workflow", str(write(tmp_path, EXAMPLE)), "--logs-dir", str(tmp_path)]) == 2
    broken = copy.deepcopy(EXAMPLE)
    broken["steps"] = broken["steps"][:2]
    monkeypatch.setattr(mar, "model_from_args", lambda _a: ScriptedModel(REVIEW))
    assert mar.main(["--workflow", str(write(tmp_path, broken, "b.json")), "--logs-dir", str(tmp_path)]) == 1
    assert mar.main(["--workflow-id", "wf-1", "--multi-agent-url", "http://127.0.0.1:9",
                     "--timeout", "0.5", "--logs-dir", str(tmp_path)]) == 3


# --------------------------------------------------------------------------- #
# Records written by ai-services/multi-agent-server (schema 1.0)
# --------------------------------------------------------------------------- #


def homs_record(decision="approve", status="approved", comments="Approved.", fallback=False):
    def stage(start, end, output):
        return {"started_at": start, "completed_at": end, "input": {}, "output": output,
                "execution": {"provider": "deterministic_fallback" if fallback else "live_ollama",
                              "fallback_used": fallback}}

    return {
        "schema_version": "1.0", "workflow_id": "006a5a08", "timestamp": "2026-10-11T06:27:50.000Z",
        "task": "Resolve a staffing shortage for the Emergency evening shift.", "input_evidence": {"x": 1},
        "planner": stage("2026-10-11T06:27:50.100Z", "2026-10-11T06:27:52.000Z",
                         {"goal": "Fill the gap", "required_evidence": ["objective", "options"],
                          "constraints": ["qualified"], "planned_steps": ["Measure the gap", "Evaluate options"],
                          "expected_result": "A proposal"}),
        "worker": stage("2026-10-11T06:27:52.100Z", "2026-10-11T06:27:55.000Z",
                        {"status": "proposal_ready", "missing_planner_evidence": [],
                         "plan_steps_followed": ["Measure the gap"],
                         "proposed_solution": {"selected_option_id": "opt-2", "selected_option": "Call in Lee"}}),
        "reviewer": stage("2026-10-11T06:27:55.100Z", "2026-10-11T06:27:58.000Z",
                          {"review_status": "validated_with_risks", "risks": ["Overtime"],
                           "recommendation": "Approve opt-2", "unsupported_claims": []}),
        "human_review": {"decision": decision, "comments": comments, "timestamp": "2026-10-11T06:27:58.859Z"},
        "final_status": status, "final_result": {"status": status},
    }


def test_homs_record_is_mapped_and_passes():
    workflow = mar.unwrap(homs_record())
    assert workflow["source_format"] == "homs-multi-agent-server/1.0"
    assert [s["agent"] for s in workflow["steps"]] == ["planner", "worker", "reviewer", "human"]
    checks = checks_for(workflow)
    assert all(c["passed"] for c in checks.values()), checks
    obs = mar.observe(workflow, "x")
    assert obs["human"]["decision"] == "approved" and obs["worker"]["result"] == "proposal_ready: Call in Lee"


def test_homs_api_view_uses_status_field():
    view = homs_record()
    view["status"] = view.pop("final_status")
    assert mar.observe(mar.unwrap(view), "api")["status"] == "approved"


def test_homs_missing_evidence_and_fallback_are_warnings():
    record = homs_record(fallback=True)
    record["worker"]["output"]["missing_planner_evidence"] = ["options"]
    workflow = mar.unwrap(record)
    results = mar.check_workflow(mar.observe(workflow, "x"))
    warned = {c["id"] for c in results if not c["passed"]}
    assert warned == {"MA4", "MA9"}
    assert mar.summarise_checks(results)["verdict"] == "pass"


def test_homs_awaiting_review_fails_and_rejection_without_reason_warns():
    record = homs_record()
    record["human_review"], record["final_status"] = None, "awaiting_human_review"
    assert not checks_for(mar.unwrap(record))["MA1"]["passed"]
    results = mar.check_workflow(mar.observe(mar.unwrap(homs_record("reject", "rejected", "")), "x"))
    reason = [c for c in results if c["title"] == "Human Review reason"][0]
    assert not reason["passed"] and reason["severity"] == "advisory"


def test_homs_failed_stage_is_reported():
    record = homs_record()
    record["reviewer"]["output"] = None
    record["reviewer"]["execution"]["error"] = "model timeout"
    record["human_review"], record["final_status"] = None, "failed"
    checks = checks_for(mar.unwrap(record))
    assert not checks["MA5"]["passed"] and not checks["MA1"]["passed"]


def test_workflow_dir_reviews_only_finalised(tmp_path, monkeypatch):
    write(tmp_path, homs_record(), "workflow-a.json")
    pending = homs_record()
    pending["human_review"], pending["final_status"] = None, "awaiting_human_review"
    write(tmp_path, pending, "workflow-b.json")
    monkeypatch.setattr(mar, "model_from_args", lambda _a: ScriptedModel(REVIEW))
    logs = tmp_path / "logs"
    assert mar.main(["--workflow-dir", str(tmp_path), "--logs-dir", str(logs)]) == 0
    record = json.loads(next(logs.glob("*.json")).read_text(encoding="utf-8"))
    assert [o["origin"] for o in record["observations"]] == [str(tmp_path / "workflow-a.json")]
