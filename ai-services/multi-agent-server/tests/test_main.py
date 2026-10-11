from __future__ import annotations

import json

import main


def test_terminal_entrypoint_completes_only_after_human_decision(
    tmp_path, monkeypatch, capsys
):
    answers = iter(["Approve", "Approved for Phase 1 demonstration"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    result = main.main(["--reports-dir", str(tmp_path)])

    assert result == 0
    output = capsys.readouterr().out
    assert output.index("[PLANNER]") < output.index("[WORKER]")
    assert output.index("[WORKER]") < output.index("[REVIEWER]")
    assert output.index("[REVIEWER]") < output.index("[HUMAN REVIEW]")
    audit_files = list(tmp_path.glob("workflow-*.json"))
    assert len(audit_files) == 1
    audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
    assert audit["human_review"]["decision"] == "approve"
    assert audit["final_status"] == "approved"


def test_terminal_eof_leaves_auditable_pending_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))

    result = main.main(["--reports-dir", str(tmp_path)])

    assert result == 2
    audit_files = list(tmp_path.glob("workflow-*.json"))
    assert len(audit_files) == 1
    audit = json.loads(audit_files[0].read_text(encoding="utf-8"))
    assert audit["final_status"] == "awaiting_human_review"
    assert audit["human_review"] is None
