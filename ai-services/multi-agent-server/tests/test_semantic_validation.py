from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agents.reviewer import Reviewer
from agents.planner import Planner
from agents.worker import Worker
from audit import AuditStore
from model_client import (
    DeterministicModelClient,
    FallbackModelClient,
    ModelError,
    OllamaModelClient,
    RoleModelClient,
    validate_agent_output,
)
from orchestrator import MultiAgentOrchestrator


TASK = "Resolve the supplied operational coverage problem."


class SequenceTransport:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def __call__(self, _payload, _timeout):
        return {"response": json.dumps(self.outputs.pop(0))}


def outputs_for(evidence):
    client = DeterministicModelClient()
    planner_context = {"task": TASK, "evidence": evidence}
    plan = client.generate(role="planner", prompt="", context=planner_context).output
    worker_context = {
        "task": TASK,
        "planner_output": plan,
        "evidence": evidence,
    }
    work = client.generate(role="worker", prompt="", context=worker_context).output
    reviewer_context = {
        "task": TASK,
        "planner_output": plan,
        "worker_output": work,
        "evidence": evidence,
    }
    review = client.generate(role="reviewer", prompt="", context=reviewer_context).output
    return planner_context, plan, worker_context, work, reviewer_context, review


def issue_codes(role, output, context):
    with pytest.raises(ModelError) as captured:
        validate_agent_output(role, output, context)
    assert captured.value.validation_type == "semantic_invalid"
    return {
        issue["code"]
        for issue in captured.value.semantic_validation["issues"]
    }


def test_planner_accepts_unique_supplied_top_level_evidence(sample_evidence):
    context, plan, *_rest = outputs_for(sample_evidence)
    report = validate_agent_output("planner", plan, context)
    assert report == {"stage": "planner", "valid": True, "issues": []}


@pytest.mark.parametrize(
    ("required", "code"),
    [
        (["objective", "invented"], "planner_unknown_evidence_key"),
        (["objective.current"], "planner_nested_path_not_allowed"),
        (["objective", "objective"], "planner_duplicate_evidence_key"),
    ],
)
def test_planner_rejects_unresolvable_or_duplicate_evidence(
    sample_evidence, required, code
):
    context, plan, *_rest = outputs_for(sample_evidence)
    invalid = copy.deepcopy(plan)
    invalid["required_evidence"] = required
    assert code in issue_codes("planner", invalid, context)


def test_planner_preserves_human_approval_constraint(sample_evidence):
    context, plan, *_rest = outputs_for(sample_evidence)
    invalid = copy.deepcopy(plan)
    approval = next(
        item["description"]
        for item in sample_evidence["constraints"]
        if item["type"] == "approval"
    )
    invalid["constraints"].remove(approval)
    assert "human_approval_requirement_missing" in issue_codes(
        "planner", invalid, context
    )


def test_deterministic_planner_only_requests_resolvable_top_level_keys(sample_evidence):
    context, plan, *_rest = outputs_for(sample_evidence)
    assert set(plan["required_evidence"]) <= set(sample_evidence)
    assert all("." not in key for key in plan["required_evidence"])
    validate_agent_output("planner", plan, context)


def test_worker_preserves_complete_supplied_option_set(sample_evidence):
    _pc, _plan, context, work, *_rest = outputs_for(sample_evidence)
    validate_agent_output("worker", work, context)
    assert [item["option_id"] for item in work["evaluated_options"]] == [
        item["id"] for item in sample_evidence["options"]
    ]


def test_worker_rejects_unknown_and_duplicate_options(sample_evidence):
    _pc, _plan, context, work, *_rest = outputs_for(sample_evidence)
    unknown = copy.deepcopy(work)
    unknown["evaluated_options"][0]["option_id"] = "not-supplied"
    assert "worker_unknown_option" in issue_codes("worker", unknown, context)

    duplicate = copy.deepcopy(work)
    duplicate["evaluated_options"].append(
        copy.deepcopy(duplicate["evaluated_options"][0])
    )
    assert "worker_duplicate_option" in issue_codes("worker", duplicate, context)


def test_worker_rejects_hard_constraint_and_eligibility_inversion(sample_evidence):
    _pc, _plan, context, work, *_rest = outputs_for(sample_evidence)
    invalid = copy.deepcopy(work)
    john = invalid["evaluated_options"][0]
    john["hard_constraint_checks"][0]["passed"] = False
    john["eligible"] = False
    codes = issue_codes("worker", invalid, context)
    assert "worker_hard_constraint_mismatch" in codes
    assert "worker_eligibility_mismatch" in codes


def test_worker_rejects_unknown_or_authoritatively_ineligible_selection(sample_evidence):
    _pc, _plan, context, work, *_rest = outputs_for(sample_evidence)
    unknown = copy.deepcopy(work)
    unknown["proposed_solution"]["selected_option_id"] = "not-supplied"
    unknown["proposed_solution"]["selected_option"] = "Not supplied"
    assert "worker_selected_option_unknown" in issue_codes("worker", unknown, context)

    evidence = copy.deepcopy(sample_evidence)
    evidence["options"][0]["attributes"]["qualified"] = False
    _pc, _plan, context, valid, *_rest = outputs_for(evidence)
    invalid = copy.deepcopy(valid)
    invalid["proposed_solution"].update(
        {
            "selected_option_id": "john",
            "selected_option": "John",
            "metric_delta": 1,
            "projected_value": 5,
        }
    )
    invalid["status"] = "proposal_ready"
    assert "worker_selected_option_ineligible" in issue_codes(
        "worker", invalid, context
    )


def test_advisory_risk_does_not_change_hard_constraint_eligibility(sample_evidence):
    _pc, _plan, context, work, *_rest = outputs_for(sample_evidence)
    sarah = next(item for item in work["evaluated_options"] if item["option_id"] == "sarah")
    assert sarah["risk_indicators"]
    assert sarah["eligible"] is True
    validate_agent_output("worker", work, context)


def test_worker_rejects_projection_mismatch_and_mutation_claim(sample_evidence):
    _pc, _plan, context, work, *_rest = outputs_for(sample_evidence)
    projection = copy.deepcopy(work)
    projection["proposed_solution"]["projected_value"] = 99
    assert "worker_projection_mismatch" in issue_codes(
        "worker", projection, context
    )

    mutation = copy.deepcopy(work)
    mutation["mutation_executed"] = True
    with pytest.raises(ModelError, match="must equal false") as captured:
        validate_agent_output("worker", mutation, context)
    assert captured.value.validation_type == "schema_invalid"


def test_worker_semantic_invalid_live_output_uses_safe_fallback(sample_evidence):
    _pc, _plan, context, valid, *_rest = outputs_for(sample_evidence)
    invalid = copy.deepcopy(valid)
    invalid["evaluated_options"][0]["eligible"] = False
    primary = OllamaModelClient(
        model="test-model",
        base_url="http://ollama.invalid",
        timeout=1,
        max_attempts=1,
        transport=SequenceTransport([invalid]),
    )
    result = Worker(FallbackModelClient(primary, DeterministicModelClient())).run(context)
    assert result.fallback_used is True
    assert result.output["proposed_solution"]["selected_option_id"] == "john"
    assert result.semantic_validation["valid"] is True
    assert result.semantic_validation["fallback_triggered"] is True
    assert result.semantic_validation["rejected_attempts"][0] == {
        "attempt": 1,
        "validation_type": "semantic_invalid",
        "issue_codes": [
            "worker_eligibility_mismatch",
            "worker_selected_option_not_eligible",
        ],
    }


def test_reviewer_rejects_unknown_or_worker_contradicting_option(sample_evidence):
    *_prefix, context, review = outputs_for(sample_evidence)
    unknown = copy.deepcopy(review)
    unknown["recommended_option_id"] = "not-supplied"
    codes = issue_codes("reviewer", unknown, context)
    assert "reviewer_unknown_option" in codes
    assert "reviewer_worker_contradiction" in codes

    contradiction = copy.deepcopy(review)
    contradiction["recommended_option_id"] = "sarah"
    assert "reviewer_worker_contradiction" in issue_codes(
        "reviewer", contradiction, context
    )


def test_reviewer_rejects_invented_structured_fact_and_free_text_finding(
    sample_evidence,
):
    *_prefix, context, review = outputs_for(sample_evidence)
    invented = copy.deepcopy(review)
    invented["validated_evidence_refs"] = [
        {"source": "evidence", "path": "objective.current", "value": 99}
    ]
    assert "reviewer_unsupported_finding" in issue_codes(
        "reviewer", invented, context
    )

    free_text = copy.deepcopy(review)
    free_text["validated_findings"] = ["Required staff is 99."]
    assert "reviewer_unsupported_finding" in issue_codes(
        "reviewer", free_text, context
    )

    unsafe_recommendation = copy.deepcopy(review)
    unsafe_recommendation["recommendation"] = (
        "Use not-supplied because it appears suitable."
    )
    assert "reviewer_unstructured_recommendation" in issue_codes(
        "reviewer", unsafe_recommendation, context
    )


def test_reviewer_rejects_missing_human_approval_and_mutation_contradiction(
    sample_evidence,
):
    *_prefix, context, review = outputs_for(sample_evidence)
    approval = copy.deepcopy(review)
    approval["human_approval_required"] = False
    assert "human_approval_requirement_missing" in issue_codes(
        "reviewer", approval, context
    )

    mutation = copy.deepcopy(review)
    mutation["mutation_verified_absent"] = False
    with pytest.raises(ModelError, match="must equal true") as captured:
        validate_agent_output("reviewer", mutation, context)
    assert captured.value.validation_type == "schema_invalid"


def test_reviewer_semantic_invalid_live_output_retries_then_accepts(sample_evidence):
    *_prefix, context, valid = outputs_for(sample_evidence)
    invalid = copy.deepcopy(valid)
    invalid["recommended_option_id"] = "not-supplied"
    client = OllamaModelClient(
        model="test-model",
        base_url="http://ollama.invalid",
        timeout=1,
        max_attempts=2,
        transport=SequenceTransport([invalid, valid]),
    )
    result = Reviewer(client).run(context)
    assert result.fallback_used is False
    assert result.semantic_validation["retry_triggered"] is True
    assert result.attempts[0]["validation_type"] == "semantic_invalid"
    assert result.attempts[1]["valid"] is True


def test_reviewer_fallback_is_conservative_and_never_invents_option(sample_evidence):
    *_prefix, context, valid = outputs_for(sample_evidence)
    invalid = copy.deepcopy(valid)
    invalid["recommended_option_id"] = "not-supplied"
    primary = OllamaModelClient(
        model="test-model",
        base_url="http://ollama.invalid",
        timeout=1,
        max_attempts=1,
        transport=SequenceTransport([invalid]),
    )
    result = Reviewer(FallbackModelClient(primary, DeterministicModelClient())).run(
        context
    )
    assert result.fallback_used is True
    assert result.output["review_status"] == "changes_required"
    assert result.output["recommended_option_id"] == context["worker_output"][
        "proposed_solution"
    ]["selected_option_id"]
    assert result.output["recommended_option_id"] in {
        item["id"] for item in sample_evidence["options"]
    }


def test_unknown_hard_constraint_operator_fails_closed(sample_evidence):
    evidence = copy.deepcopy(sample_evidence)
    evidence["constraints"][0]["operator"] = "not_equals"
    _pc, _plan, context, work, *_rest = outputs_for(evidence)
    assert "hard_constraint_operator_unsupported" in issue_codes(
        "worker", work, context
    )


def test_audit_records_semantic_rejection_fallback_and_conservative_review(
    tmp_path, sample_evidence
):
    _pc, _plan, _context, valid, *_rest = outputs_for(sample_evidence)
    invalid = copy.deepcopy(valid)
    invalid["evaluated_options"][0]["eligible"] = False
    deterministic = DeterministicModelClient()
    worker = FallbackModelClient(
        OllamaModelClient(
            model="test-model",
            base_url="http://ollama.invalid",
            timeout=1,
            max_attempts=1,
            transport=SequenceTransport([invalid]),
        ),
        deterministic,
    )
    clients = RoleModelClient(
        {"planner": deterministic, "worker": worker, "reviewer": deterministic}
    )
    orchestrator = MultiAgentOrchestrator(
        planner=Planner(clients),
        worker=Worker(clients),
        reviewer=Reviewer(clients),
        audit_store=AuditStore(tmp_path),
    )

    record, audit_path = orchestrator.start(task=TASK, evidence=sample_evidence)
    persisted = json.loads(Path(audit_path).read_text(encoding="utf-8"))

    semantic = persisted["worker"]["execution"]["semantic_validation"]
    assert semantic["valid"] is True
    assert semantic["fallback_triggered"] is True
    assert semantic["rejected_attempts"][0]["validation_type"] == "semantic_invalid"
    assert record["reviewer"]["output"]["review_status"] == "changes_required"
    assert record["reviewer"]["output"]["recommended_option_id"] == "john"
    assert record["final_status"] == "awaiting_human_review"
