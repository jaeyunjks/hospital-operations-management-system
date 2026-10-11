from __future__ import annotations

import json

import pytest

from model_client import (
    DeterministicModelClient,
    FallbackModelClient,
    ModelError,
    OllamaModelClient,
    parse_json_object,
    validate_agent_output,
)
from models import AGENT_OUTPUT_SCHEMAS


TASK = "Resolve a staffing shortage for the Emergency evening shift."


class SequenceTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, payload, timeout):
        self.calls.append({"payload": payload, "timeout": timeout})
        return {"response": self.responses.pop(0)}


def role_cases(evidence):
    deterministic = DeterministicModelClient()
    planner_context = {"task": TASK, "evidence": evidence}
    plan = deterministic.generate(
        role="planner", prompt="", context=planner_context
    ).output
    worker_context = {
        "task": TASK,
        "planner_output": plan,
        "evidence": evidence,
    }
    work = deterministic.generate(
        role="worker", prompt="", context=worker_context
    ).output
    reviewer_context = {
        "task": TASK,
        "planner_output": plan,
        "worker_output": work,
        "evidence": evidence,
    }
    review = deterministic.generate(
        role="reviewer", prompt="", context=reviewer_context
    ).output
    return {
        "planner": (planner_context, plan),
        "worker": (worker_context, work),
        "reviewer": (reviewer_context, review),
    }


@pytest.mark.parametrize("role", ["planner", "worker", "reviewer"])
def test_valid_live_style_agent_outputs(role, sample_evidence):
    context, expected = role_cases(sample_evidence)[role]
    transport = SequenceTransport([json.dumps(expected)])
    client = OllamaModelClient(
        model="test-model",
        base_url="http://ollama.invalid",
        timeout=5,
        max_attempts=1,
        transport=transport,
    )

    result = client.generate(role=role, prompt="role prompt", context=context)

    assert result.output == expected
    assert result.provider == "live_ollama"
    assert result.fallback_used is False
    assert len(result.attempts) == 1
    assert result.attempts[0]["valid"] is True
    assert transport.calls[0]["payload"]["format"] == AGENT_OUTPUT_SCHEMAS[role]


def test_malformed_json_triggers_corrective_retry(sample_evidence):
    context, valid = role_cases(sample_evidence)["planner"]
    transport = SequenceTransport(["{not valid", json.dumps(valid)])
    client = OllamaModelClient(
        model="test-model",
        base_url="http://ollama.invalid",
        timeout=5,
        max_attempts=2,
        transport=transport,
    )

    result = client.generate(role="planner", prompt="role prompt", context=context)

    assert len(result.attempts) == 2
    assert "invalid JSON" in result.attempts[0]["validation_error"]
    assert result.attempts[1]["valid"] is True
    retry_prompt = transport.calls[1]["payload"]["prompt"]
    assert "CORRECTION REQUIRED" in retry_prompt
    assert "Validation error:" in retry_prompt
    assert "Required JSON Schema:" in retry_prompt


def test_json_code_fence_is_safely_removed(sample_evidence):
    context, valid = role_cases(sample_evidence)["planner"]
    raw = "```json\n" + json.dumps(valid) + "\n```"
    transport = SequenceTransport([raw])
    client = OllamaModelClient(
        model="test-model",
        base_url="http://ollama.invalid",
        timeout=5,
        max_attempts=1,
        transport=transport,
    )

    result = client.generate(role="planner", prompt="role prompt", context=context)

    assert result.output == valid
    assert result.attempts[0]["normalisation"] == "json_code_fence_removed"


def test_minimal_surrounding_prose_extracts_one_object(sample_evidence):
    _context, valid = role_cases(sample_evidence)["planner"]
    parsed, normalisation = parse_json_object(
        "Here is the requested object: " + json.dumps(valid) + " End."
    )
    assert parsed == valid
    assert normalisation == "json_object_extracted"


def test_wrong_field_type_is_rejected(sample_evidence):
    context, invalid = role_cases(sample_evidence)["planner"]
    invalid = dict(invalid)
    invalid["planned_steps"] = "inspect then decide"

    with pytest.raises(ModelError, match="planned_steps must be array"):
        validate_agent_output("planner", invalid, context)


def test_missing_required_field_is_rejected(sample_evidence):
    context, invalid = role_cases(sample_evidence)["reviewer"]
    invalid = dict(invalid)
    invalid.pop("recommendation")

    with pytest.raises(ModelError, match="missing required field.*recommendation"):
        validate_agent_output("reviewer", invalid, context)


def test_retry_exhaustion_preserves_every_invalid_response(sample_evidence):
    context, invalid = role_cases(sample_evidence)["reviewer"]
    invalid = dict(invalid)
    invalid["mutation_verified_absent"] = False
    transport = SequenceTransport([json.dumps(invalid)] * 3)
    client = OllamaModelClient(
        model="test-model",
        base_url="http://ollama.invalid",
        timeout=5,
        max_attempts=3,
        transport=transport,
    )

    with pytest.raises(ModelError, match="after 3 attempts") as captured:
        client.generate(role="reviewer", prompt="role prompt", context=context)

    assert len(captured.value.attempts) == 3
    assert all(not attempt["valid"] for attempt in captured.value.attempts)
    assert all(attempt["raw_response"] for attempt in captured.value.attempts)


def test_fallback_provenance_never_claims_live_output(sample_evidence):
    context, invalid = role_cases(sample_evidence)["planner"]
    invalid = dict(invalid)
    invalid["planned_steps"] = "not an array"
    primary = OllamaModelClient(
        model="test-model",
        base_url="http://ollama.invalid",
        timeout=5,
        max_attempts=1,
        transport=SequenceTransport([json.dumps(invalid)]),
    )
    client = FallbackModelClient(primary, DeterministicModelClient())

    result = client.generate(role="planner", prompt="role prompt", context=context)

    assert result.provider == "deterministic_fallback"
    assert result.model is None
    assert result.fallback_used is True
    assert "remained invalid" in result.fallback_reason
    assert len(result.attempts) == 1

