from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.planner import Planner
from agents.reviewer import Reviewer
from agents.worker import Worker
from audit import AuditStore
from model_client import DeterministicModelClient, ModelError, _validate_output
from models import HumanDecision, HumanReview, utc_now
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
                "status": "proposed",
                "gap": {},
                "evaluated_options": [],
                "proposed_solution": "Assign somebody",
                "limitations": [],
                "mutation_executed": False,
            },
        )
