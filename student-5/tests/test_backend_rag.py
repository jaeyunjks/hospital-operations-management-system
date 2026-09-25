"""Student 5 API boundary tests for shared RAG documentation guidance."""

from __future__ import annotations

import pytest

from services import rag_client


def answered_result():
    return {
        "schema_version": "1.0",
        "status": "answered",
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
    }


def insufficient_result():
    return {
        "schema_version": "1.0",
        "status": "insufficient_context",
        "answer": "The knowledge base does not contain enough relevant information.",
        "confidence": "none",
        "citations": [],
        "reason": "no_relevant_context",
    }


def install_adapter(monkeypatch, *, result=None, error=None, calls=None):
    calls = calls if calls is not None else []

    class FakeAdapter:
        def __init__(self, **configuration):
            calls.append(("config", configuration))

        def ask(self, question):
            calls.append(("ask", question))
            if error:
                raise error
            return result or answered_result()

    import routes.rag_routes as routes
    monkeypatch.setattr(routes, "StaffShiftRAGClient", FakeAdapter)
    return calls


def enable(client):
    client.application.config.update(
        RAG_ENABLED=True,
        RAG_SERVER_URL="http://127.0.0.1:8100",
        RAG_TIMEOUT=100.0,
    )


def test_rag_is_disabled_by_default(client, monkeypatch):
    calls = []

    def forbidden_network(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("disabled RAG must not open an HTTP connection")

    monkeypatch.setattr(rag_client.urllib.request, "urlopen", forbidden_network)
    client.application.config["RAG_ENABLED"] = False
    response = client.post(
        "/api/rag/ask", json={"question": "How is coverage calculated?"})
    assert response.status_code == 503
    assert response.json == {
        "error": "rag_unavailable",
        "message": "Staff & Shift guidance via RAG is not enabled.",
    }
    assert calls == []


def test_manager_receives_answered_result(client, monkeypatch):
    enable(client)
    calls = install_adapter(monkeypatch, result=answered_result())
    response = client.post(
        "/api/rag/ask", json={"question": "  How is coverage calculated?  "})
    assert response.status_code == 200
    assert response.json == answered_result()
    assert ("ask", "How is coverage calculated?") in calls
    assert ("config", {
        "enabled": True,
        "server_url": "http://127.0.0.1:8100",
        "timeout": 100.0,
    }) in calls


def test_insufficient_context_is_a_successful_outcome(client, monkeypatch):
    enable(client)
    install_adapter(monkeypatch, result=insufficient_result())
    response = client.post(
        "/api/rag/ask", json={"question": "Who is rostered right now?"})
    assert response.status_code == 200
    assert response.json == insufficient_result()


def test_manager_guard_runs_before_rag(client, monkeypatch):
    enable(client)
    calls = install_adapter(monkeypatch)
    response = client.post(
        "/api/rag/ask",
        json={"question": "How is coverage calculated?"},
        headers={"X-HOMS-Role": "Employee", "X-HOMS-Staff-Id": "1"},
    )
    assert response.status_code == 403
    assert response.json["error"] == "forbidden"
    assert calls == []


@pytest.mark.parametrize("payload", [
    {},
    {"question": ""},
    {"question": "   "},
    {"question": 5},
    {"question": "x" * 501},
])
def test_invalid_question_is_rejected_before_rag(client, monkeypatch, payload):
    enable(client)
    calls = install_adapter(monkeypatch)
    response = client.post("/api/rag/ask", json=payload)
    assert response.status_code == 400
    assert response.json["error"] == "validation_error"
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("feature", "student-1"),
    ("top_k", 8),
    ("server_url", "http://example.invalid"),
    ("endpoint", "/sources"),
])
def test_browser_cannot_override_rag_controls(
        client, monkeypatch, field, value):
    enable(client)
    calls = install_adapter(monkeypatch)
    response = client.post("/api/rag/ask", json={
        "question": "How is coverage calculated?", field: value,
    })
    assert response.status_code == 400
    assert response.json["error"] == "validation_error"
    assert response.json["details"]["fields"] == [field]
    assert calls == []


@pytest.mark.parametrize("failure,status,code", [
    (rag_client.RAGUnavailable(), 503, "rag_unavailable"),
    (rag_client.RAGTimeout(), 504, "rag_timeout"),
    (rag_client.RAGInvalidResponse(), 502, "rag_invalid_response"),
])
def test_adapter_failures_are_safely_mapped(
        client, monkeypatch, failure, status, code):
    enable(client)
    install_adapter(monkeypatch, error=failure)
    response = client.post(
        "/api/rag/ask", json={"question": "How is coverage calculated?"})
    assert response.status_code == status
    assert response.json["error"] == code
    text = response.get_data(as_text=True)
    assert "127.0.0.1" not in text
    assert "Traceback" not in text


def test_api_index_lists_rag_but_health_does_not_depend_on_it(client):
    client.application.config["RAG_ENABLED"] = False
    assert client.get("/api").json["endpoints"]["rag"] == [
        "POST   /api/rag/ask"
    ]
    health = client.get("/health")
    assert health.status_code == 200
    assert "rag" not in health.json
