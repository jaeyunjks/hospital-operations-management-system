from __future__ import annotations

import shutil
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))

import agentic_loop  # noqa: E402
import testing_review as tr  # noqa: E402

EXAMPLES = AGENT_DIR / "examples" / "test-evidence"
REVIEW = {"assessment": "ok", "recommendations": [], "next_action": "None.", "outcome": "pass"}

FAILED_OUTPUT = """\x1b[1mruff (security rules)\x1b[0m....................................Failed
- hook id: ruff
- exit code: 1

student-4/backend/app.py:12:5: S105 Possible hardcoded password
Detect secrets.....................................................Passed
pip-audit..........................................(no files to check)Skipped
"""


def model(*replies):
    replies = list(replies)
    return lambda _prompt: replies.pop(0)


def by_key(checks):
    return {(c["id"], c.get("subject")): c for c in checks}


def test_parse_precommit_reads_status_hook_id_and_details():
    hooks = tr.parse_precommit(FAILED_OUTPUT)["hooks"]
    assert [(h["name"], h["status"]) for h in hooks] == [
        ("ruff (security rules)", "failed"), ("Detect secrets", "passed"), ("pip-audit", "skipped")]
    assert hooks[0]["hook_id"] == "ruff" and any("S105" in d for d in hooks[0]["details"])


def test_precommit_checks_flag_failed_and_skipped_scans():
    checks = by_key(tr.check_precommit(tr.parse_precommit(FAILED_OUTPUT)))
    assert not checks[("T3", "ruff-security")]["passed"]
    assert checks[("T3", "detect-secrets")]["passed"]
    assert not checks[("T2", "pip-audit")]["passed"]


def test_equivalent_scanners_are_accepted():
    text = "bandit.....Passed\ngitleaks.....Passed\nsafety check.....Passed\n"
    assert all(c["passed"] for c in tr.check_precommit(tr.parse_precommit(text)))


def test_missing_precommit_report_fails():
    assert not tr.check_precommit(None)[0]["passed"]


def test_student_needs_two_passing_tests_and_no_failures(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text('<testsuite><testcase name="a"/><testcase name="b"><failure message="500"/></testcase>'
                   "</testsuite>", encoding="utf-8")
    report = tr.parse_junit(xml)
    assert (report["passed"], report["failed"]) == (1, 1)
    checks = by_key(tr.check_student("student-4", [report], []))
    assert not checks[("T5", "student-4")]["passed"]
    assert checks[("T6", "student-4")]["severity"] == "advisory"


def test_workflow_run_must_be_own_workflow_and_succeed(tmp_path):
    run = {"file": "r.json", "workflowName": "student-3", "conclusion": "failure"}
    detail = by_key(tr.check_student("student-4", [], [run]))[("T6", "student-4")]["detail"]
    assert "not student-4.yml" in detail and "failure" in detail


def test_discover_and_full_review_on_examples():
    precommit, junit, runs = tr.discover(EXAMPLES)
    assert precommit.name == "pre-commit.txt" and junit[0][0] == "student-4" and runs[0][0] == "student-4"
    result = tr.TestingReview(generate=model(REVIEW), students=["student-4"], precommit=precommit,
                              junit=junit, runs=runs, log=lambda _m: None).run()
    assert result["summary"]["verdict"] == "pass", result["checks"]


def test_missing_students_fail_the_review():
    precommit, junit, runs = tr.discover(EXAMPLES)
    result = tr.TestingReview(generate=model(REVIEW), students=["student-1", "student-4"],
                              precommit=precommit, junit=junit, runs=runs, log=lambda _m: None).run()
    assert result["summary"]["failed"] == ["T4 [student-1]"]


def test_main_discovers_evidence_dir_and_writes_logs(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(EXAMPLES, repo / "docs" / "test-evidence")
    monkeypatch.setattr(tr, "model_from_args", lambda _a: model(REVIEW))
    code = agentic_loop.main(["--mode", "testing", "--students", "4", "--repo-root", str(repo)])
    assert code == 0
    markdown = next((repo / "docs" / "agent-logs" / "group").glob("*-testing-review.md")).read_text("utf-8")
    assert "Testing Report Review" in markdown and "| student-4 | 2 | 2 | 0 | success |" in markdown


def test_main_configuration_errors(tmp_path):
    assert tr.main(["--repo-root", str(tmp_path)]) == 2
    assert tr.main(["--junit", str(tmp_path / "unknown.xml"), "--logs-dir", str(tmp_path)]) == 2
