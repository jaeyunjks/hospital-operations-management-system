from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))

import agentic_loop  # noqa: E402
import cloud_review as cr  # noqa: E402

EXAMPLE = AGENT_DIR / "examples" / "cloud-probes.example.json"
REVIEW = {"assessment": "Healthy.", "retry": [], "recommendations": [], "recommended_decision": "release",
          "next_action": "Release.", "outcome": "pass"}
RUN = {"workflowName": "cloud-deployment", "conclusion": "success", "status": "completed", "headSha": "abc",
       "url": "https://github.com/x/y/actions/runs/9", "jobs": [
           {"name": "validate", "conclusion": "success", "startedAt": "2026-10-21T01:00:00Z",
            "completedAt": "2026-10-21T01:03:00Z"},
           {"name": "deploy", "conclusion": "success", "startedAt": "2026-10-21T01:03:05Z",
            "completedAt": "2026-10-21T01:08:00Z"}]}


def ok(data, status=200):
    return {"reachable": True, "status": status, "duration_ms": 30,
            "body": json.dumps({"success": True, "data": data, "error": None})}


class FakeCloud:
    """Answers like the Student 4 backend deployed with AI disabled."""

    def __init__(self, overrides=None):
        self.calls, self.overrides = [], overrides or {}

    def __call__(self, method, url, body, headers, timeout):
        self.calls.append((method, url, body, headers))
        path = url.split(".io", 1)[1]
        if (method, path) in self.overrides:
            value = self.overrides[(method, path)]
            return value.pop(0) if isinstance(value, list) else value
        bed = {"bed_id": 41, "bed_number": "CLOUD-TEST-01", "status": "available"}
        routes = {
            ("GET", "/"): {"reachable": True, "status": 200, "body": "<html>HOMS</html>", "duration_ms": 10},
            ("GET", "/health"): ok({"database": {"ok": True}, "ai": {"ok": False}}),
            ("GET", "/api/beds"): ok([bed]),
            ("POST", "/api/beds"): ok(bed, 201),
            ("GET", "/api/beds/41"): ok(bed),
            ("PUT", "/api/beds/41"): ok({**bed, **(body or {})}),
            ("DELETE", "/api/beds/41"): ok({"deleted": True}),
            ("GET", "/api/mcp/status"): ok({"enabled": False}),
            ("GET", "/api/rag/status"): ok({"enabled": False}),
            ("GET", "/api/multi-agent/status"): ok({"enabled": False}),
        }
        return routes.get((method, path), {"reachable": True, "status": 404, "body": "{}", "duration_ms": 5})


def model(*replies):
    replies, prompts = list(replies), []

    def generate(prompt):
        prompts.append(prompt)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    generate.prompts = prompts
    return generate


def run_file(tmp_path):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(RUN), encoding="utf-8")
    return path


def review(tmp_path, send, generate, **kwargs):
    return cr.CloudReview(generate=generate, probes=cr.load_probes(EXAMPLE), workflow_run=run_file(tmp_path),
                          send=send, sleep=lambda _s: None, log=lambda _m: None, **kwargs).run()


def by_key(checks):
    return {(c["id"], c.get("subject")): c for c in checks}


def test_full_deployment_passes_with_writes(tmp_path):
    send = FakeCloud()
    result = review(tmp_path, send, model(REVIEW), allow_writes=True)
    assert result["summary"]["verdict"] == "pass", result["checks"]
    assert ("PUT", "https://student4-backend.REPLACE.australiaeast.azurecontainerapps.io/api/beds/41",
            {"status": "maintenance"}, {}) in send.calls


def test_write_probes_are_skipped_without_flag(tmp_path):
    send = FakeCloud()
    result = review(tmp_path, send, model(REVIEW))
    assert all(method == "GET" for method, *_ in send.calls)
    assert not by_key(result["checks"])[("C2", None)]["passed"]


def test_enabled_ai_service_fails_its_check(tmp_path):
    send = FakeCloud({("GET", "/api/rag/status"): ok({"enabled": True})})
    checks = by_key(review(tmp_path, send, model(REVIEW), allow_writes=True)["checks"])
    assert not checks[("C3", "rag")]["passed"] and checks[("C3", "mcp")]["passed"]


def test_adapt_can_reprobe_a_cold_start_once(tmp_path):
    cold = {"reachable": True, "status": 503, "body": "starting", "duration_ms": 20000}
    send = FakeCloud({("GET", "/"): [cold, {"reachable": True, "status": 200, "body": "ok", "duration_ms": 9}]})
    generate = model({**REVIEW, "retry": ["FE1", "AI2", "nope"]}, REVIEW)
    result = review(tmp_path, send, generate, allow_writes=True)
    assert [r["id"] for r in result["rounds"][1]["results"]] == ["FE1"]
    assert result["summary"]["verdict"] == "pass" and len(result["adapt_rounds"]) == 2
    assert '"retry_allowed": true' in generate.prompts[0]


def test_unreachable_deployment_raises(tmp_path):
    def down(*_args):
        return {"reachable": False, "error": "connection refused", "duration_ms": 1}

    with pytest.raises(cr.EvidenceUnavailable):
        review(tmp_path, down, model(REVIEW))


def test_workflow_run_must_be_cloud_deployment_and_succeed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({**RUN, "workflowName": "student-4", "conclusion": "failure"}), encoding="utf-8")
    _summary, checks = cr.check_workflow_run(path)
    assert not checks[0]["passed"] and "failure" in checks[0]["detail"]
    _summary, checks = cr.check_workflow_run(None)
    assert not checks[0]["passed"]


def test_header_tokens_come_from_environment_and_stay_out_of_evidence(tmp_path, monkeypatch):
    spec = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    spec["headers"] = {"Authorization": "Bearer ${env:HOMS_CLOUD_TOKEN}"}
    path = tmp_path / "probes.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="HOMS_CLOUD_TOKEN"):
        cr.load_probes(path)
    monkeypatch.setenv("HOMS_CLOUD_TOKEN", "s3cret-token")
    send = FakeCloud()
    result = cr.CloudReview(generate=model(REVIEW), probes=cr.load_probes(path), send=send,
                            log=lambda _m: None).run()
    assert send.calls[0][3] == {"Authorization": "Bearer s3cret-token"}
    assert "s3cret-token" not in json.dumps(result)


def test_human_decision_recording():
    summary = {"verdict": "fail"}
    args = argparse.Namespace(decision="release", reviewer="Asher", reason="demo")
    decision = cr.human_decision(args, summary, {"result": None})
    assert decision["decision"] == "release" and decision["conflicts_with_checks"]
    pending = cr.human_decision(argparse.Namespace(decision=None, reviewer=None, reason=None), summary, {})
    assert pending["decision"] == "pending"


def test_main_writes_cloud_review_with_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "model_from_args", lambda _a: model(REVIEW))
    original = cr.CloudReview.__init__

    def patched(self, **kwargs):
        original(self, **{**kwargs, "send": FakeCloud()})

    monkeypatch.setattr(cr.CloudReview, "__init__", patched)
    code = agentic_loop.main(["--mode", "cloud", "--probes", str(EXAMPLE), "--workflow-run", str(run_file(tmp_path)),
                              "--allow-writes", "--decision", "release", "--reviewer", "Asher",
                              "--reason", "All checks passed", "--logs-dir", str(tmp_path / "logs")])
    assert code == 0
    markdown = next((tmp_path / "logs").glob("*-cloud-deployment-review.md")).read_text(encoding="utf-8")
    assert "Decision: **RELEASE**" in markdown and "Reviewer: Asher" in markdown


def test_bad_probe_file_is_a_configuration_error(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"probes": [{"id": "X", "kind": "ai_disabled", "url": "https://a"}]}), "utf-8")
    assert cr.main(["--probes", str(path), "--logs-dir", str(tmp_path)]) == 2
