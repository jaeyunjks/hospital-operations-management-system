"""HTTP contract tests for Student 5's shared Multi-Agent adapter."""

from __future__ import annotations

import copy
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
    pending = status == "awaiting_human_review"
    return {
        "schema_version": "1.0",
        "workflow_id": WORKFLOW_ID,
        "status": status,
        "task": task,
        "planner": copy.deepcopy(stage),
        "worker": copy.deepcopy(stage),
        "reviewer": copy.deepcopy(stage),
        "human_review": None if pending else {"decision": "approve"},
        "final_result": None if pending else {"mutation_executed": False},
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
    calls = install_response(monkeypatch, workflow())
    supplied = copy.deepcopy(EVIDENCE)

    result = adapter().start_workflow(TASK, supplied)
    request, timeout = calls[0]

    assert request.full_url == "http://127.0.0.1:8200/workflows"
    assert request.method == "POST"
    assert request.headers["Content-type"] == "application/json"
    assert timeout == 600.0
    assert json.loads(request.data) == {"task": TASK, "evidence": EVIDENCE}
    assert supplied == EVIDENCE
    assert result == workflow()


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


@pytest.mark.parametrize("status", ["in_progress", "approved", "failed", "other"])
def test_start_requires_the_human_review_gate(monkeypatch, status):
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
