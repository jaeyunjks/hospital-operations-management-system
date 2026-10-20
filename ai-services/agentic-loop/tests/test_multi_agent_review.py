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
    assert not check["passed"] and "needs a comment" in check["detail"]


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
