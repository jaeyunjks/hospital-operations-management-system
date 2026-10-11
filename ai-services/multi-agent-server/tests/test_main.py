from __future__ import annotations

import json
from argparse import Namespace

import main


def test_role_model_defaults_and_cli_overrides():
    defaults = Namespace(
        model=None,
        planner_model=None,
        worker_model=None,
        reviewer_model=None,
    )
    assert main.resolve_role_models(defaults, environment={}) == {
        "planner": "llama3.1:8b",
        "worker": "llama3.2:3b",
        "reviewer": "deepseek-r1:8b",
    }

    args = main.parse_args(
        [
            "--model",
            "global:test",
            "--planner-model",
            "planner:test",
            "--worker-model",
            "worker:test",
            "--reviewer-model",
            "reviewer:test",
        ]
    )
    assert main.resolve_role_models(args, environment={}) == {
        "planner": "planner:test",
        "worker": "worker:test",
        "reviewer": "reviewer:test",
    }


def test_role_environment_overrides_legacy_global_model():
    args = Namespace(
        model="global:test",
        planner_model=None,
        worker_model=None,
        reviewer_model=None,
    )
    resolved = main.resolve_role_models(
        args,
        environment={"HOMS_MULTI_AGENT_WORKER_MODEL": "worker:environment"},
    )

    assert resolved == {
        "planner": "global:test",
        "worker": "worker:environment",
        "reviewer": "global:test",
    }


def test_live_client_builds_one_ollama_client_per_default_role(monkeypatch):
    for name in (
        "OLLAMA_MODEL",
        "HOMS_MULTI_AGENT_PLANNER_MODEL",
        "HOMS_MULTI_AGENT_WORKER_MODEL",
        "HOMS_MULTI_AGENT_REVIEWER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    args = main.parse_args(["--model-mode", "ollama"])

    client = main.build_model_client(args)

    assert {
        role: role_client.model for role, role_client in client.clients.items()
    } == {
        "planner": "llama3.1:8b",
        "worker": "llama3.2:3b",
        "reviewer": "deepseek-r1:8b",
    }


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
