from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.planner import Planner
from agents.reviewer import Reviewer
from agents.worker import Worker
from audit import AuditStore
from model_client import (
    DeterministicModelClient,
    ModelError,
    OllamaModelClient,
    RoleModelClient,
    _validate_output,
)
from models import GenerationResult, HumanDecision, HumanReview, utc_now
from orchestrator import MultiAgentOrchestrator


TASK = "Resolve a staffing shortage for the Emergency evening shift."


def build_orchestrator(reports_dir: Path, events=None):
    client = DeterministicModelClient()
    event_log = events if events is not None else []
    return MultiAgentOrchestrator(
        planner=Planner(client),
        worker=Worker(client),
        reviewer=Reviewer(client),
        audit_store=AuditStore(reports_dir),
        on_stage=lambda stage, payload: event_log.append((stage, payload)),
    )


def decision(value=HumanDecision.APPROVE, comments="Reviewed"):
    return HumanReview(decision=value, comments=comments, timestamp=utc_now())


def test_complete_sequence_and_handoffs_are_audited(tmp_path, sample_evidence):
    events = []
    record, audit_path = build_orchestrator(tmp_path, events).run(
        task=TASK,
        evidence=sample_evidence,
        request_human_review=lambda _record: decision(),
    )

    assert [stage for stage, _payload in events] == [
        "workflow_started",
        "planner",
        "worker",
        "reviewer",
        "human_review_required",
        "workflow_completed",
    ]
    assert record["worker"]["input"]["planner_output"] == record["planner"]["output"]
    assert record["reviewer"]["input"]["worker_output"] == record["worker"]["output"]
    assert record["reviewer"]["input"]["planner_output"] == record["planner"]["output"]
    assert record["final_status"] == "approved"
    assert Path(audit_path).is_file()


def test_worker_selects_lowest_risk_eligible_option(sample_evidence):
    client = DeterministicModelClient()
    plan = Planner(client).run({"task": TASK, "evidence": sample_evidence}).output
    output = Worker(client).run(
        {"task": TASK, "planner_output": plan, "evidence": sample_evidence}
    ).output

    assert output["gap"]["deficit"] == 1
    assert output["proposed_solution"]["selected_option"] == "John"
    assert output["proposed_solution"]["projected_value"] == 5
    assert output["mutation_executed"] is False


def test_reviewer_independently_detects_wrong_worker_projection(sample_evidence):
    client = DeterministicModelClient()
    plan = Planner(client).run({"task": TASK, "evidence": sample_evidence}).output
    worker_output = Worker(client).run(
        {"task": TASK, "planner_output": plan, "evidence": sample_evidence}
    ).output
    worker_output["proposed_solution"]["projected_value"] = 99

    review = Reviewer(client).run(
        {
            "task": TASK,
            "planner_output": plan,
            "worker_output": worker_output,
            "evidence": sample_evidence,
        }
    ).output

    assert review["review_status"] == "changes_required"
    assert any("does not match" in claim for claim in review["unsupported_claims"])
    assert review["human_approval_required"] is True


def test_audit_is_awaiting_review_before_callback_runs(tmp_path, sample_evidence):
    observed = {}

    def review_callback(record):
        audit_path = tmp_path / f"workflow-{record['workflow_id']}.json"
        checkpoint = json.loads(audit_path.read_text(encoding="utf-8"))
        observed.update(checkpoint)
        return decision()

    build_orchestrator(tmp_path).run(
        task=TASK, evidence=sample_evidence, request_human_review=review_callback
    )

    assert observed["final_status"] == "awaiting_human_review"
    assert observed["human_review"] is None
    assert observed["final_result"] is None


@pytest.mark.parametrize(
    ("human_decision", "expected_status"),
    [
        (HumanDecision.CORRECT, "corrected"),
        (HumanDecision.REJECT, "rejected"),
        (HumanDecision.PARTIALLY_ACCEPT, "partially_accepted"),
    ],
)
def test_human_decisions_are_preserved(
    tmp_path, sample_evidence, human_decision, expected_status
):
    record, _path = build_orchestrator(tmp_path).run(
        task=TASK,
        evidence=sample_evidence,
        request_human_review=lambda _record: decision(
            human_decision, "Human correction or decision note"
        ),
    )

    assert record["final_status"] == expected_status
    assert record["human_review"]["decision"] == human_decision.value
    assert record["human_review"]["comments"] == "Human correction or decision note"
    assert record["final_result"]["mutation_executed"] is False


def test_audit_store_never_overwrites_an_existing_workflow(tmp_path):
    store = AuditStore(tmp_path)
    record = {"workflow_id": "fixed-id", "value": 1}
    store.create(record)

    with pytest.raises(FileExistsError):
        store.create({"workflow_id": "fixed-id", "value": 2})
    assert json.loads((tmp_path / "workflow-fixed-id.json").read_text())["value"] == 1


def test_live_model_contract_rejects_unstructured_worker_proposal():
    with pytest.raises(ModelError, match="proposed_solution"):
        _validate_output(
            "worker",
            {
                "status": "proposal_ready",
                "plan_steps_followed": [],
                "missing_planner_evidence": [],
                "gap": {
                    "metric": "assigned_staff",
                    "current": 4,
                    "target": 5,
                    "deficit": 1,
                },
                "evaluated_options": [],
                "proposed_solution": "Assign somebody",
                "limitations": [],
                "mutation_executed": False,
            },
        )


def test_orchestrator_refuses_invalid_worker_handoff(tmp_path, sample_evidence):
    deterministic = DeterministicModelClient()

    class InvalidWorkerClient:
        def __init__(self):
            self.roles = []

        def generate(self, *, role, prompt, context):
            self.roles.append(role)
            if role == "planner":
                return deterministic.generate(role=role, prompt=prompt, context=context)
            return GenerationResult(
                output={
                    "status": "proposal_ready",
                    "plan_steps_followed": context["planner_output"]["planned_steps"],
                    "missing_planner_evidence": [],
                    "gap": {
                        "metric": "assigned_staff",
                        "current": 4,
                        "target": 5,
                        "deficit": 1,
                    },
                    "evaluated_options": [],
                    "proposed_solution": {
                        "selected_option_id": "john",
                        "selected_option": "John",
                        "metric_delta": 1,
                        "projected_value": 5,
                        "reasoning": ["evidence"],
                    },
                    "limitations": [],
                    "mutation_executed": False,
                },
                provider="live_ollama",
                model="test-model",
            )

    client = InvalidWorkerClient()
    orchestrator = MultiAgentOrchestrator(
        planner=Planner(client),
        worker=Worker(client),
        reviewer=Reviewer(client),
        audit_store=AuditStore(tmp_path),
    )

    with pytest.raises(ModelError, match="worker_missing_option"):
        orchestrator.run(
            task=TASK,
            evidence=sample_evidence,
            request_human_review=lambda _record: pytest.fail(
                "human review must not run after an invalid Worker output"
            ),
        )

    audit = json.loads(next(tmp_path.glob("workflow-*.json")).read_text())
    assert client.roles == ["planner", "worker"]
    assert audit["worker"]["output"] is None
    assert audit["reviewer"] is None
    assert audit["human_review"] is None
    assert audit["final_status"] == "failed"


def test_live_provenance_and_human_gate_are_audited(tmp_path, sample_evidence):
    deterministic = DeterministicModelClient()
    planner_context = {"task": TASK, "evidence": sample_evidence}
    plan = deterministic.generate(
        role="planner", prompt="", context=planner_context
    ).output
    worker_context = {
        "task": TASK,
        "planner_output": plan,
        "evidence": sample_evidence,
    }
    work = deterministic.generate(role="worker", prompt="", context=worker_context).output
    review = deterministic.generate(
        role="reviewer",
        prompt="",
        context={
            "task": TASK,
            "planner_output": plan,
            "worker_output": work,
            "evidence": sample_evidence,
        },
    ).output
    outputs = {"planner": plan, "worker": work, "reviewer": review}
    models = {
        "planner": "llama3.1:8b",
        "worker": "llama3.2:3b",
        "reviewer": "deepseek-r1:8b",
    }
    client = RoleModelClient(
        {
            role: OllamaModelClient(
                model=model,
                base_url="http://ollama.invalid",
                timeout=5,
                transport=lambda _payload, _timeout, output=outputs[role]: {
                    "response": json.dumps(output)
                },
            )
            for role, model in models.items()
        }
    )
    human_gate_reached = []
    orchestrator = MultiAgentOrchestrator(
        planner=Planner(client),
        worker=Worker(client),
        reviewer=Reviewer(client),
        audit_store=AuditStore(tmp_path),
    )

    record, _path = orchestrator.run(
        task=TASK,
        evidence=sample_evidence,
        request_human_review=lambda _record: (
            human_gate_reached.append(True) or decision()
        ),
    )

    assert human_gate_reached == [True]
    assert record["final_status"] == "approved"
    for role in ("planner", "worker", "reviewer"):
        assert record[role]["execution"]["provider"] == "live_ollama"
        assert record[role]["execution"]["model"] == models[role]
        assert record[role]["execution"]["fallback_used"] is False
        assert record[role]["execution"]["attempt_count"] == 1
        assert record[role]["execution"]["attempts"][0]["model"] == models[role]


def test_exhausted_live_attempts_are_retained_in_failed_audit(
    tmp_path, sample_evidence
):
    class ExhaustedClient:
        def generate(self, *, role, prompt, context):
            raise ModelError(
                "planner output remained invalid after 1 attempt",
                provider="live_ollama",
                model="test-model",
                attempts=(
                    {
                        "attempt": 1,
                        "provider": "live_ollama",
                        "model": "test-model",
                        "raw_response": '{"goal": 7}',
                        "normalisation": "none",
                        "validation_error": "planner output.goal must be string",
                        "valid": False,
                    },
                ),
            )

    client = ExhaustedClient()
    orchestrator = MultiAgentOrchestrator(
        planner=Planner(client),
        worker=Worker(client),
        reviewer=Reviewer(client),
        audit_store=AuditStore(tmp_path),
    )

    with pytest.raises(ModelError, match="remained invalid"):
        orchestrator.run(
            task=TASK,
            evidence=sample_evidence,
            request_human_review=lambda _record: pytest.fail(
                "human review must not run after retry exhaustion"
            ),
        )

    audit = json.loads(next(tmp_path.glob("workflow-*.json")).read_text())
    assert audit["planner"]["execution"]["provider"] == "live_ollama"
    assert audit["planner"]["execution"]["attempt_count"] == 1
    assert audit["planner"]["execution"]["attempts"][0]["raw_response"] == '{"goal": 7}'
    assert audit["planner"]["execution"]["attempts"][0]["valid"] is False
    assert audit["final_status"] == "failed"
