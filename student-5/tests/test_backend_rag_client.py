"""HTTP contract tests for Student 5's narrow shared-RAG adapter."""

from __future__ import annotations

import copy
import inspect
import json
import socket
import urllib.error

import pytest

from services import rag_client


def answered(question="How is shift coverage calculated?"):
    return {
        "schema_version": "1.0",
        "status": "answered",
        "question": question,
        "feature": "student-5",
        "answer": "Coverage is calculated per shift [S1].",
        "confidence": "high",
        "citations": [{
            "id": "S1",
            "source": "student-5/shift-planning-and-coverage.md",
            "title": "Shift Planning and Coverage",
            "section": "Per-shift coverage calculation",
            "score": 0.82,
            "snippet": "Coverage counts active assignments.",
        }],
        "reason": None,
        "retrieval": {
            "top_score": 0.82,
            "threshold": 0.62,
            "considered": 4,
            "relevant": 3,
        },
        "models": {
            "embedding": "nomic-embed-text",
            "generation": "llama3.2:3b",
        },
        "duration_ms": 125,
    }


def insufficient(question="Who is rostered right now?"):
    body = answered(question)
    body.update(
        status="insufficient_context",
        answer="The knowledge base does not contain enough relevant information.",
        confidence="none",
        citations=[],
        reason="no_relevant_context",
        retrieval={
            "top_score": 0.5,
            "threshold": 0.62,
            "considered": 4,
            "relevant": 0,
        },
    )
    return body


class FakeResponse:
    def __init__(self, body):
        self.raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.raw[:size] if size >= 0 else self.raw


def adapter(**kwargs):
    return rag_client.StaffShiftRAGClient(
        enabled=True,
        server_url="http://127.0.0.1:8100",
        timeout=100,
        **kwargs,
    )


def install_response(monkeypatch, body, calls=None):
    calls = calls if calls is not None else []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(body)

    monkeypatch.setattr(rag_client.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_posts_only_query_with_fixed_feature_and_top_k(monkeypatch):
    calls = install_response(monkeypatch, answered())
    result = adapter().ask("How is shift coverage calculated?")
    request, timeout = calls[0]
    assert request.full_url == "http://127.0.0.1:8100/query"
    assert request.method == "POST"
    assert timeout == 100
    assert json.loads(request.data) == {
        "question": "How is shift coverage calculated?",
        "feature": "student-5",
        "top_k": 4,
    }
    assert result == {
        "schema_version": "1.0",
        "status": "answered",
        "answer": "Coverage is calculated per shift [S1].",
        "confidence": "high",
        "citations": answered()["citations"],
        "reason": None,
    }


def test_public_operation_accepts_only_a_question():
    assert list(inspect.signature(
        rag_client.StaffShiftRAGClient.ask
    ).parameters) == ["self", "question"]


def test_disabled_client_never_opens_http(monkeypatch):
    calls = []
    monkeypatch.setattr(
        rag_client.urllib.request, "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    disabled = rag_client.StaffShiftRAGClient(enabled=False)
    with pytest.raises(rag_client.RAGDisabledError):
        disabled.ask("How is coverage calculated?")
    assert calls == []


def test_valid_insufficient_context_is_returned_as_success(monkeypatch):
    body = insufficient()
    install_response(monkeypatch, body)
    assert adapter().ask(body["question"])["status"] == "insufficient_context"


def test_grouped_valid_citations_are_accepted(monkeypatch):
    body = answered()
    second = dict(body["citations"][0])
    second.update(
        id="S2",
        source="shared/homs-overview.md",
        title="HOMS Overview",
        section="Architecture",
    )
    body["citations"].append(second)
    body["answer"] = "Coverage uses Student 5 scheduling facts [S1, S2]."
    install_response(monkeypatch, body)
    result = adapter().ask(body["question"])
    assert [citation["id"] for citation in result["citations"]] == ["S1", "S2"]


@pytest.mark.parametrize("change", [
    lambda body: body.update(schema_version="2.0"),
    lambda body: body.update(status="other"),
    lambda body: body.update(confidence="certain"),
    lambda body: body.update(feature="student-4"),
    lambda body: body.update(question="another question"),
    lambda body: body.update(citations=[]),
    lambda body: body["citations"][0].update(source="student-4/rooms.md"),
    lambda body: body["citations"][0].update(id="S9"),
    lambda body: body["citations"][0].update(score=1.5),
    lambda body: body["citations"][0].pop("snippet"),
    lambda body: body.update(answer="Coverage is calculated per shift."),
    lambda body: body["retrieval"].update(relevant=5),
    lambda body: body.update(extra="unexpected"),
])
def test_malformed_answered_contract_is_rejected(monkeypatch, change):
    body = copy.deepcopy(answered())
    change(body)
    install_response(monkeypatch, body)
    with pytest.raises(rag_client.RAGInvalidResponse):
        adapter().ask("How is shift coverage calculated?")


@pytest.mark.parametrize("change", [
    lambda body: body.update(confidence="low"),
    lambda body: body.update(citations=answered()["citations"]),
    lambda body: body.update(reason=None),
    lambda body: body.update(reason="unexpected"),
])
def test_malformed_insufficient_contract_is_rejected(monkeypatch, change):
    body = copy.deepcopy(insufficient())
    change(body)
    install_response(monkeypatch, body)
    with pytest.raises(rag_client.RAGInvalidResponse):
        adapter().ask("Who is rostered right now?")


@pytest.mark.parametrize("question", [None, 5, "", "   ", "x" * 501])
def test_question_is_bounded_before_http(monkeypatch, question):
    calls = []
    monkeypatch.setattr(
        rag_client.urllib.request, "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    with pytest.raises(rag_client.RAGInputError):
        adapter().ask(question)
    assert calls == []


def test_unavailable_server_has_no_fallback(monkeypatch):
    calls = []

    def unavailable(request, timeout):
        calls.append((request.full_url, timeout))
        raise urllib.error.URLError(OSError("private connection detail"))

    monkeypatch.setattr(rag_client.urllib.request, "urlopen", unavailable)
    with pytest.raises(rag_client.RAGUnavailable):
        adapter().ask("How is coverage calculated?")
    assert calls == [("http://127.0.0.1:8100/query", 100)]
    assert "database_client" not in vars(rag_client)
    assert "ollama_client" not in vars(rag_client)
    assert "mcp_client" not in vars(rag_client)


@pytest.mark.parametrize("failure", [
    socket.timeout("slow"),
    urllib.error.URLError(socket.timeout("slow")),
])
def test_timeout_is_classified(monkeypatch, failure):
    def timeout(*args, **kwargs):
        raise failure

    monkeypatch.setattr(rag_client.urllib.request, "urlopen", timeout)
    with pytest.raises(rag_client.RAGTimeout):
        adapter().ask("How is coverage calculated?")


def test_bad_json_is_invalid_response(monkeypatch):
    install_response(monkeypatch, b"not json")
    with pytest.raises(rag_client.RAGInvalidResponse):
        adapter().ask("How is shift coverage calculated?")


def test_oversized_response_is_rejected(monkeypatch):
    install_response(monkeypatch, b"x" * (rag_client.MAX_RESPONSE_BYTES + 1))
    with pytest.raises(rag_client.RAGInvalidResponse):
        adapter().ask("How is shift coverage calculated?")


@pytest.mark.parametrize("url", [
    "", "ftp://localhost", "http://u:p@host", "http://host?feature=student-1",
    "http://host/rag", "http://host#query",
])
def test_invalid_config_is_a_contract_failure(monkeypatch, url):
    calls = []
    monkeypatch.setattr(
        rag_client.urllib.request, "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    invalid = rag_client.StaffShiftRAGClient(
        enabled=True, server_url=url, timeout=100)
    with pytest.raises(rag_client.RAGInvalidResponse):
        invalid.ask("How is coverage calculated?")
    assert calls == []


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), True, "100"])
def test_invalid_timeout_is_a_contract_failure(monkeypatch, timeout):
    calls = []
    monkeypatch.setattr(
        rag_client.urllib.request, "urlopen",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    invalid = rag_client.StaffShiftRAGClient(
        enabled=True, server_url="http://127.0.0.1:8100", timeout=timeout)
    with pytest.raises(rag_client.RAGInvalidResponse):
        invalid.ask("How is coverage calculated?")
    assert calls == []
