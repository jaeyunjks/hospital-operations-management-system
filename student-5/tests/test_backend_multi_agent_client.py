"""HTTP contract tests for Student 5's shared Multi-Agent adapter."""

from __future__ import annotations

import copy
import io
import json
import socket
import urllib.error

import pytest

from services import multi_agent_client


WORKFLOW_ID = "12345678-1234-4678-9234-567812345678"
TASK = "Review the selected shift."
EVIDENCE = {
    "schema_version": "1.0",
    "evidence_type": "student5_staffing_recovery",
    "objective": {"current": 1, "target": 2},
}


def workflow(status="awaiting_human_review", task=TASK):
    stage = {
        "started_at": "2026-10-14T01:00:00Z",
        "completed_at": "2026-10-14T01:00:01Z",
        "output": {"result": "complete"},
        "execution": {"provider": "deterministic_fallback"},
    }
    pending = status in {"in_progress", "awaiting_human_review"}
    failed = status == "failed"
    decision = {
        "approved": "approve",
        "corrected": "correct",
        "rejected": "reject",
        "partially_accepted": "partially_accept",
    }.get(status)
    comments = "Required correction." if decision == "correct" else ""
    initial = status == "in_progress"
    return {
        "schema_version": "1.0",
        "workflow_id": WORKFLOW_ID,
        "status": status,
        "task": task,
        "planner": None if initial else copy.deepcopy(stage),
        "worker": None if initial else copy.deepcopy(stage),
        "reviewer": None if initial else copy.deepcopy(stage),
        "human_review": None if pending or failed else {
            "decision": decision,
            "comments": comments,
            "timestamp": "2026-10-14T01:01:00Z",
        },
        "final_result": (
            None if pending else {
                "status": "failed",
                "error": "generation failed",
                "mutation_executed": False,
            } if failed else {
                "status": status,
                "proposal": {"recommendation": "staff-1"},
                "review": {"status": "valid"},
                "human_decision": decision,
                "human_comments_or_correction": comments,
                "mutation_executed": False,
            }
        ),
    }


class FakeResponse:
    def __init__(self, body):
        self.raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.raw[:size] if size >= 0 else self.raw


def adapter(**overrides):
    options = {
        "base_url": "http://127.0.0.1:8200",
        "timeout": 600.0,
    }
    options.update(overrides)
    return multi_agent_client.MultiAgentClient(**options)


def install_response(monkeypatch, body, calls=None):
    calls = calls if calls is not None else []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(body)

    monkeypatch.setattr(
        multi_agent_client.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_start_posts_only_task_and_unchanged_evidence(monkeypatch):
    calls = install_response(monkeypatch, workflow(status="in_progress"))
    supplied = copy.deepcopy(EVIDENCE)

    result = adapter().start_workflow(TASK, supplied)
    request, timeout = calls[0]

    assert request.full_url == "http://127.0.0.1:8200/workflows"
    assert request.method == "POST"
    assert request.headers["Content-type"] == "application/json"
    assert timeout == 600.0
    assert json.loads(request.data) == {"task": TASK, "evidence": EVIDENCE}
    assert supplied == EVIDENCE
    assert result == workflow(status="in_progress")


def test_get_and_review_use_the_existing_lifecycle_endpoints(monkeypatch):
    calls = []
    responses = [workflow(), workflow(status="approved")]

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(responses.pop(0))

    monkeypatch.setattr(
        multi_agent_client.urllib.request, "urlopen", fake_urlopen)
    client = adapter()

    assert client.get_workflow(WORKFLOW_ID)["status"] == "awaiting_human_review"
    assert client.submit_review(
        WORKFLOW_ID, "approve", "Reviewed by manager."
    )["status"] == "approved"
    assert calls[0][0].full_url.endswith(f"/workflows/{WORKFLOW_ID}")
    assert calls[0][0].method == "GET"
    assert calls[1][0].full_url.endswith(
        f"/workflows/{WORKFLOW_ID}/review")
    assert json.loads(calls[1][0].data) == {
        "decision": "approve", "comment": "Reviewed by manager."
    }


@pytest.mark.parametrize("decision,status", [
    ("approve", "approved"),
    ("correct", "corrected"),
    ("reject", "rejected"),
    ("partially_accept", "partially_accepted"),
])
def test_review_supports_exact_shared_decisions(
        monkeypatch, decision, status):
    calls = install_response(monkeypatch, workflow(status=status))
    comment = "Required correction." if decision == "correct" else ""

    result = adapter().submit_review(WORKFLOW_ID, decision, comment)

    assert result["status"] == status
    assert json.loads(calls[0][0].data) == {
        "decision": decision, "comment": comment
    }


@pytest.mark.parametrize("operation", ["get", "review"])
def test_invalid_workflow_input_is_rejected_before_network(
        monkeypatch, operation):
    calls = []
    monkeypatch.setattr(
        multi_agent_client.urllib.request,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(multi_agent_client.MultiAgentInputError):
        if operation == "get":
            adapter().get_workflow("not-a-uuid")
        else:
            adapter().submit_review("not-a-uuid", "approve")
    assert calls == []


@pytest.mark.parametrize("decision,comment", [
    ("Approve", ""),
    ("partial", ""),
    ("correct", ""),
    ("approve", 1),
    ("approve", "x" * 10_001),
])
def test_invalid_review_input_is_rejected_before_network(
        monkeypatch, decision, comment):
    calls = []
    monkeypatch.setattr(
        multi_agent_client.urllib.request,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with pytest.raises(multi_agent_client.MultiAgentInputError):
        adapter().submit_review(WORKFLOW_ID, decision, comment)
    assert calls == []


def shared_error(code, message, status):
    body = json.dumps({
        "schema_version": "1.0",
        "error": {"code": code, "message": message},
    }).encode("utf-8")

    def rejected(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, status, message, {}, io.BytesIO(body)
        )

    return rejected


def test_shared_not_found_is_preserved(monkeypatch):
    monkeypatch.setattr(
        multi_agent_client.urllib.request,
        "urlopen",
        shared_error("not_found", "Workflow not found", 404),
    )

    with pytest.raises(multi_agent_client.MultiAgentNotFound):
        adapter().get_workflow(WORKFLOW_ID)


def test_shared_lifecycle_conflict_is_preserved(monkeypatch):
    monkeypatch.setattr(
        multi_agent_client.urllib.request,
        "urlopen",
        shared_error(
            "invalid_workflow_state",
            "workflow is not awaiting Human Review",
            409,
        ),
    )

    with pytest.raises(multi_agent_client.MultiAgentConflict):
        adapter().submit_review(WORKFLOW_ID, "approve")


@pytest.mark.parametrize("status", [404, 409])
def test_malformed_shared_lifecycle_error_is_rejected(monkeypatch, status):
    def rejected(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, status, "bad", {}, io.BytesIO(b"not json")
        )

    monkeypatch.setattr(
        multi_agent_client.urllib.request, "urlopen", rejected
    )
    operation = (
        lambda: adapter().get_workflow(WORKFLOW_ID)
    ) if status == 404 else (
        lambda: adapter().submit_review(WORKFLOW_ID, "approve")
    )

    with pytest.raises(multi_agent_client.MultiAgentInvalidResponse):
        operation()


def test_get_accepts_a_persisted_failure_with_incomplete_stages(monkeypatch):
    body = workflow(status="failed")
    body["reviewer"] = None
    install_response(monkeypatch, body)

    assert adapter().get_workflow(WORKFLOW_ID) == body


def test_connection_failure_has_no_fake_fallback(monkeypatch):
    def unavailable(*args, **kwargs):
        raise urllib.error.URLError(OSError("private network detail"))

    monkeypatch.setattr(
        multi_agent_client.urllib.request, "urlopen", unavailable)
    with pytest.raises(multi_agent_client.MultiAgentUnavailable):
        adapter().start_workflow(TASK, EVIDENCE)


@pytest.mark.parametrize("failure", [
    socket.timeout("slow"),
    urllib.error.URLError(socket.timeout("slow")),
])
def test_timeout_is_classified(monkeypatch, failure):
    def timeout(*args, **kwargs):
        raise failure

    monkeypatch.setattr(
        multi_agent_client.urllib.request, "urlopen", timeout)
    with pytest.raises(multi_agent_client.MultiAgentTimeout):
        adapter().start_workflow(TASK, EVIDENCE)


def test_non_success_response_is_classified(monkeypatch):
    def rejected(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, None)

    monkeypatch.setattr(
        multi_agent_client.urllib.request, "urlopen", rejected)
    with pytest.raises(multi_agent_client.MultiAgentHTTPError):
        adapter().start_workflow(TASK, EVIDENCE)


@pytest.mark.parametrize("body", [
    b"not json",
    [],
    {"schema_version": "1.0"},
])
def test_malformed_response_is_rejected(monkeypatch, body):
    install_response(monkeypatch, body)
    with pytest.raises(multi_agent_client.MultiAgentInvalidResponse):
        adapter().start_workflow(TASK, EVIDENCE)


def test_missing_or_invalid_workflow_id_is_rejected(monkeypatch):
    for workflow_id in (None, "not-a-uuid"):
        body = workflow()
        body["workflow_id"] = workflow_id
        install_response(monkeypatch, body)
        with pytest.raises(multi_agent_client.MultiAgentInvalidResponse):
            adapter().start_workflow(TASK, EVIDENCE)


@pytest.mark.parametrize(
    "status", ["awaiting_human_review", "approved", "failed", "other"]
)
def test_start_requires_a_new_in_progress_workflow(monkeypatch, status):
    install_response(monkeypatch, workflow(status=status))
    with pytest.raises(multi_agent_client.MultiAgentInvalidResponse):
        adapter().start_workflow(TASK, EVIDENCE)


@pytest.mark.parametrize("url", [
    "", "ftp://localhost", "http://u:p@host", "http://host/api",
    "http://host?x=1", "http://host#fragment",
])
def test_invalid_base_url_is_rejected_before_network(monkeypatch, url):
    calls = []
    monkeypatch.setattr(
        multi_agent_client.urllib.request,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    with pytest.raises(multi_agent_client.MultiAgentInvalidResponse):
        adapter(base_url=url).start_workflow(TASK, EVIDENCE)
    assert calls == []


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), True, "600"])
def test_invalid_timeout_is_rejected_before_network(monkeypatch, timeout):
    calls = []
    monkeypatch.setattr(
        multi_agent_client.urllib.request,
        "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    with pytest.raises(multi_agent_client.MultiAgentInvalidResponse):
        adapter(timeout=timeout).start_workflow(TASK, EVIDENCE)
    assert calls == []
