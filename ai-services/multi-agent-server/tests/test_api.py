from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

import api
from agents.planner import Planner
from agents.reviewer import Reviewer
from agents.worker import Worker
from audit import AuditStore
from model_client import (
    DeterministicModelClient,
    FallbackModelClient,
    ModelError,
    OllamaModelClient,
    RoleModelClient,
)
from orchestrator import MultiAgentOrchestrator


TASK = "Resolve a staffing shortage for the Emergency evening shift."
LOCAL = {"Host": "127.0.0.1:8200"}
MODELS = {
    "planner": "llama3.1:8b",
    "worker": "llama3.2:3b",
    "reviewer": "deepseek-r1:8b",
}


class ImmediateExecutor:
    def submit(self, function, *args):
        function(*args)


class ManualExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, function, *args):
        self.jobs.append((function, args))

    def run_next(self):
        function, args = self.jobs.pop(0)
        function(*args)


def build_orchestrator(reports_dir: Path, client=None):
    client = client or DeterministicModelClient()
    return MultiAgentOrchestrator(
        planner=Planner(client),
        worker=Worker(client),
        reviewer=Reviewer(client),
        audit_store=AuditStore(reports_dir),
    )


def build_client(reports_dir: Path, model_client=None, executor=None):
    config = api.ServerConfig(
        host="127.0.0.1", port=8200, allow_docker_host=False
    )
    return api.create_app(
        build_orchestrator(reports_dir, model_client),
        config,
        workflow_executor=executor or ImmediateExecutor(),
    ).test_client()


def create_workflow(client, evidence):
    return client.post(
        "/workflows",
        json={"task": TASK, "evidence": evidence},
        headers=LOCAL,
    )


def post_review(client, workflow_id, decision, comment="Reviewed"):
    return client.post(
        f"/workflows/{workflow_id}/review",
        json={"decision": decision, "comment": comment},
        headers=LOCAL,
    )


def live_style_client(evidence):
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
    review = deterministic.generate(
        role="reviewer",
        prompt="",
        context={
            "task": TASK,
            "planner_output": plan,
            "worker_output": work,
            "evidence": evidence,
        },
    ).output
    outputs = {"planner": plan, "worker": work, "reviewer": review}
    return RoleModelClient(
        {
            role: OllamaModelClient(
                model=model,
                base_url="http://ollama.invalid",
                timeout=1,
                transport=lambda _payload, _timeout, output=outputs[role]: {
                    "response": json.dumps(output)
                },
            )
            for role, model in MODELS.items()
        }
    )


def test_health_does_not_run_a_workflow(tmp_path):
    client = build_client(tmp_path)

    response = client.get("/health", headers=LOCAL)

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "service": "multi-agent-server",
    }
    assert list(tmp_path.glob("workflow-*.json")) == []


def test_create_returns_pollable_workflow_before_agents_run(
    tmp_path, sample_evidence
):
    executor = ManualExecutor()
    client = build_client(tmp_path, executor=executor)
    response = create_workflow(client, sample_evidence)
    body = response.get_json()

    assert response.status_code == 202
    assert body["workflow_id"]
    assert body["status"] == "in_progress"
    assert body["planner"] is None
    assert body["worker"] is None
    assert body["reviewer"] is None
    assert body["human_review"] is None
    assert body["final_result"] is None
    assert response.headers["Location"] == f"/workflows/{body['workflow_id']}"
    assert response.headers["Retry-After"] == "1"
    assert (tmp_path / f"workflow-{body['workflow_id']}.json").is_file()
    assert client.get(response.headers["Location"], headers=LOCAL).get_json()[
        "status"
    ] == "in_progress"
    assert post_review(client, body["workflow_id"], "approve").status_code == 409

    executor.run_next()
    completed = client.get(response.headers["Location"], headers=LOCAL).get_json()
    assert completed["status"] == "awaiting_human_review"
    assert completed["planner"]["output"]["goal"] == TASK
    assert completed["worker"]["output"]["mutation_executed"] is False
    assert completed["reviewer"]["output"]["human_approval_required"] is True


def test_get_polls_partial_background_progress(tmp_path, sample_evidence):
    deterministic = DeterministicModelClient()
    worker_started = Event()
    release_worker = Event()

    class BlockingWorkerClient:
        def generate(self, *, role, prompt, context):
            if role == "worker":
                worker_started.set()
                assert release_worker.wait(timeout=2)
            return deterministic.generate(role=role, prompt=prompt, context=context)

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        client = build_client(
            tmp_path, BlockingWorkerClient(), executor=executor
        )
        created = create_workflow(client, sample_evidence).get_json()
        assert worker_started.wait(timeout=1)

        partial = client.get(
            f"/workflows/{created['workflow_id']}", headers=LOCAL
        ).get_json()
        assert partial["status"] == "in_progress"
        assert partial["planner"]["output"]["goal"] == TASK
        assert partial["worker"] is None
        assert partial["reviewer"] is None

        release_worker.set()
        future = executor.submit(lambda: None)
        future.result(timeout=2)
        completed = client.get(
            f"/workflows/{created['workflow_id']}", headers=LOCAL
        ).get_json()
        assert completed["status"] == "awaiting_human_review"
    finally:
        release_worker.set()
        executor.shutdown(wait=True)


def test_get_reconstructs_persisted_workflow_after_app_restart(
    tmp_path, sample_evidence
):
    created = create_workflow(build_client(tmp_path), sample_evidence).get_json()
    restarted_client = build_client(tmp_path)

    response = restarted_client.get(
        f"/workflows/{created['workflow_id']}", headers=LOCAL
    )

    assert response.status_code == 200
    assert created["status"] == "in_progress"
    assert response.get_json()["status"] == "awaiting_human_review"
    assert response.get_json()["workflow_id"] == created["workflow_id"]


def test_get_can_restore_submitted_evidence_without_changing_default_view(
    tmp_path, sample_evidence
):
    client = build_client(tmp_path)
    created = create_workflow(client, sample_evidence).get_json()

    default = client.get(
        f"/workflows/{created['workflow_id']}", headers=LOCAL
    ).get_json()
    restored = client.get(
        f"/workflows/{created['workflow_id']}?include_evidence=true",
        headers=LOCAL,
    ).get_json()

    assert "submitted_evidence" not in default
    assert restored["submitted_evidence"] == sample_evidence
    assert restored["workflow_id"] == created["workflow_id"]


def test_history_requires_evidence_scope_and_returns_only_matching_audits(
    tmp_path, sample_evidence
):
    client = build_client(tmp_path)
    first_evidence = {
        **sample_evidence,
        "evidence_type": "student5_staffing_recovery",
        "current_state": {"shift": {"shift_id": 11}},
    }
    second_evidence = {
        **sample_evidence,
        "evidence_type": "student5_staffing_recovery",
        "current_state": {"shift": {"shift_id": 12}},
    }
    first = create_workflow(client, first_evidence).get_json()
    create_workflow(client, second_evidence)

    missing_scope = client.get("/workflows", headers=LOCAL)
    response = client.get(
        "/workflows?evidence_type=student5_staffing_recovery"
        "&evidence_path=current_state.shift.shift_id&evidence_value=11&limit=10",
        headers=LOCAL,
    )
    body = response.get_json()

    assert missing_scope.status_code == 400
    assert response.status_code == 200
    assert body["count"] == 1
    assert body["workflows"][0]["workflow_id"] == first["workflow_id"]
    assert "submitted_evidence" not in body["workflows"][0]


def test_app_restart_resumes_a_persisted_in_progress_workflow(
    tmp_path, sample_evidence
):
    paused_executor = ManualExecutor()
    first_client = build_client(tmp_path, executor=paused_executor)
    created = create_workflow(first_client, sample_evidence).get_json()
    assert created["status"] == "in_progress"

    restarted_client = build_client(tmp_path)
    resumed = restarted_client.get(
        f"/workflows/{created['workflow_id']}", headers=LOCAL
    ).get_json()

    assert resumed["workflow_id"] == created["workflow_id"]
    assert resumed["status"] == "awaiting_human_review"
    assert resumed["human_review"] is None


def test_restarted_app_can_finalise_persisted_workflow(tmp_path, sample_evidence):
    created = create_workflow(build_client(tmp_path), sample_evidence).get_json()
    restarted_client = build_client(tmp_path)

    response = post_review(restarted_client, created["workflow_id"], "approve")

    assert response.status_code == 200
    assert response.get_json()["status"] == "approved"


def test_unknown_workflow_returns_404(tmp_path):
    client = build_client(tmp_path)
    response = client.get(
        "/workflows/00000000-0000-4000-8000-000000000000", headers=LOCAL
    )
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [
        ("approve", "approved"),
        ("reject", "rejected"),
        ("partially_accept", "partially_accepted"),
    ],
)
def test_human_review_decisions_finalise_persisted_workflow(
    tmp_path, sample_evidence, decision, expected_status
):
    client = build_client(tmp_path)
    workflow_id = create_workflow(client, sample_evidence).get_json()["workflow_id"]

    response = post_review(client, workflow_id, decision)
    body = response.get_json()
    persisted = json.loads(
        (tmp_path / f"workflow-{workflow_id}.json").read_text(encoding="utf-8")
    )

    assert response.status_code == 200
    assert body["status"] == expected_status
    assert body["human_review"]["decision"] == decision
    assert body["final_result"]["mutation_executed"] is False
    assert persisted["final_status"] == expected_status
    assert persisted["human_review"]["decision"] == decision


def test_correct_requires_comment_and_preserves_correction(tmp_path, sample_evidence):
    client = build_client(tmp_path)
    workflow_id = create_workflow(client, sample_evidence).get_json()["workflow_id"]

    missing = post_review(client, workflow_id, "correct", "")
    corrected = post_review(client, workflow_id, "correct", "Use another option")

    assert missing.status_code == 400
    assert corrected.status_code == 200
    assert corrected.get_json()["status"] == "corrected"
    assert corrected.get_json()["human_review"]["comments"] == "Use another option"


def test_review_cannot_run_before_review_gate(tmp_path, sample_evidence):
    client = build_client(tmp_path)
    created = create_workflow(client, sample_evidence).get_json()
    store = AuditStore(tmp_path)
    record = store.read(created["workflow_id"])
    record["final_status"] = "in_progress"
    store.update(record)

    response = post_review(client, created["workflow_id"], "approve")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "invalid_workflow_state"


def test_completed_workflow_cannot_be_reviewed_again(tmp_path, sample_evidence):
    client = build_client(tmp_path)
    workflow_id = create_workflow(client, sample_evidence).get_json()["workflow_id"]
    assert post_review(client, workflow_id, "approve").status_code == 200

    repeated = post_review(client, workflow_id, "reject")

    assert repeated.status_code == 409
    assert repeated.get_json()["error"]["code"] == "invalid_workflow_state"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"task": "", "evidence": {"value": 1}},
        {"task": TASK, "evidence": []},
        {"task": TASK, "evidence": {"value": 1}, "unexpected": True},
    ],
)
def test_malformed_workflow_requests_are_rejected(tmp_path, payload):
    response = build_client(tmp_path).post(
        "/workflows", json=payload, headers=LOCAL
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_invalid_review_decision_is_rejected(tmp_path, sample_evidence):
    client = build_client(tmp_path)
    workflow_id = create_workflow(client, sample_evidence).get_json()["workflow_id"]
    response = post_review(client, workflow_id, "override")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"


def test_separate_requests_use_distinct_audit_files(tmp_path, sample_evidence):
    client = build_client(tmp_path)
    first = create_workflow(client, sample_evidence).get_json()["workflow_id"]
    second = create_workflow(client, sample_evidence).get_json()["workflow_id"]

    assert first != second
    assert sorted(path.name for path in tmp_path.glob("workflow-*.json")) == sorted(
        [f"workflow-{first}.json", f"workflow-{second}.json"]
    )


def test_role_specific_live_provenance_is_returned_and_persisted(
    tmp_path, sample_evidence
):
    client = build_client(tmp_path, live_style_client(sample_evidence))

    created = create_workflow(client, sample_evidence).get_json()
    body = client.get(
        f"/workflows/{created['workflow_id']}", headers=LOCAL
    ).get_json()
    persisted = json.loads(
        (tmp_path / f"workflow-{body['workflow_id']}.json").read_text()
    )

    for role, model in MODELS.items():
        assert body[role]["execution"] == {
            "provider": "live_ollama",
            "model": model,
            "fallback_used": False,
            "fallback_reason": None,
            "attempt_count": 1,
        }
        assert persisted[role]["execution"]["model"] == model
        assert persisted[role]["execution"]["attempts"][0]["model"] == model


def test_fallback_provenance_never_claims_live_success(tmp_path, sample_evidence):
    deterministic = DeterministicModelClient()
    invalid = deterministic.generate(
        role="planner", prompt="", context={"task": TASK, "evidence": sample_evidence}
    ).output
    invalid["planned_steps"] = "not an array"
    planner = FallbackModelClient(
        OllamaModelClient(
            model=MODELS["planner"],
            base_url="http://ollama.invalid",
            timeout=1,
            max_attempts=1,
            transport=lambda _payload, _timeout: {"response": json.dumps(invalid)},
        ),
        deterministic,
    )
    routed = RoleModelClient(
        {"planner": planner, "worker": deterministic, "reviewer": deterministic}
    )

    client = build_client(tmp_path, routed)
    created = create_workflow(client, sample_evidence).get_json()
    body = client.get(
        f"/workflows/{created['workflow_id']}", headers=LOCAL
    ).get_json()
    audit = json.loads(
        (tmp_path / f"workflow-{body['workflow_id']}.json").read_text()
    )

    assert body["planner"]["execution"]["provider"] == "deterministic_fallback"
    assert body["planner"]["execution"]["model"] is None
    assert body["planner"]["execution"]["fallback_used"] is True
    assert audit["planner"]["execution"]["attempts"][0]["model"] == "llama3.1:8b"


def test_model_failure_is_reported_by_polling_persisted_audit(
    tmp_path, sample_evidence
):
    class FailedClient:
        def generate(self, *, role, prompt, context):
            raise ModelError(
                f"{role} model unavailable",
                provider="live_ollama",
                model=MODELS[role],
            )

    response = create_workflow(build_client(tmp_path, FailedClient()), sample_evidence)
    created = response.get_json()
    polled = build_client(tmp_path).get(
        f"/workflows/{created['workflow_id']}", headers=LOCAL
    )
    body = polled.get_json()

    assert response.status_code == 202
    assert created["status"] == "in_progress"
    assert polled.status_code == 200
    assert body["status"] == "failed"
    assert body["human_review"] is None
    assert body["final_result"]["mutation_executed"] is False
    assert len(list(tmp_path.glob("workflow-*.json"))) == 1


def test_server_defaults_to_loopback_and_rejects_unexpected_hosts(tmp_path):
    assert api.load_server_config({}) == api.ServerConfig(
        host="127.0.0.1", port=8200, allow_docker_host=False
    )
    response = build_client(tmp_path).get(
        "/health", headers={"Host": "evil.example:8200"}
    )
    assert response.status_code == 421


@pytest.mark.parametrize(
    "environment",
    [
        {"HOMS_MULTI_AGENT_HOST": "0.0.0.0"},
        {"HOMS_MULTI_AGENT_PORT": "0"},
        {"HOMS_MULTI_AGENT_PORT": "not-a-port"},
        {"HOMS_MULTI_AGENT_ALLOW_DOCKER_HOST": "yes"},
    ],
)
def test_invalid_server_configuration_is_rejected(environment):
    with pytest.raises(ValueError):
        api.load_server_config(environment)
