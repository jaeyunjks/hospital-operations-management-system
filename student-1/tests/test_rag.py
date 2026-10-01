import json
from unittest.mock import Mock

import pytest

from backend.app import create_app
from backend.routes import rag_endpoints
from backend.services import rag_client


def answered_response(question="How are admissions scheduled?"):
    return {
        "schema_version": "1.0",
        "status": "answered",
        "question": question,
        "feature": "student-1",
        "answer": "Admissions use 15-minute blocks [S1].",
        "confidence": "medium",
        "citations": [{
            "id": "S1",
            "source": "student-1/admissions-and-reconciliation.md",
            "title": "Admissions and Reconciliation",
            "section": "Admission scheduling",
            "score": 0.74,
            "snippet": "Admission times use 15-minute blocks.",
        }],
        "reason": None,
        "retrieval": {"top_score": 0.74, "threshold": 0.62, "considered": 4, "relevant": 2},
        "models": {"embedding": "nomic-embed-text", "generation": "llama3.2:3b"},
        "duration_ms": 1200,
    }


class FakeHTTPResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return json.dumps(self.body).encode("utf-8")


def test_client_fixes_feature_and_validates_scoped_citations(monkeypatch):
    response_body = answered_response()
    urlopen = Mock(return_value=FakeHTTPResponse(response_body))
    monkeypatch.setattr(rag_client.urllib.request, "urlopen", urlopen)
    client = rag_client.PatientAdmissionRAGClient(
        enabled=True, server_url="http://127.0.0.1:8100", timeout=100,
    )

    result = client.ask("  How are admissions scheduled?  ")

    call = urlopen.call_args.args[0]
    assert call.full_url == "http://127.0.0.1:8100/query"
    assert json.loads(call.data) == {
        "question": "How are admissions scheduled?",
        "feature": "student-1",
        "top_k": 4,
    }
    assert result["status"] == "answered"
    assert result["citations"] == response_body["citations"]


def test_disabled_client_does_not_call_server(monkeypatch):
    urlopen = Mock()
    monkeypatch.setattr(rag_client.urllib.request, "urlopen", urlopen)
    client = rag_client.PatientAdmissionRAGClient(False, "http://127.0.0.1:8100", 100)

    with pytest.raises(rag_client.RAGDisabled):
        client.ask("How are admissions scheduled?")

    urlopen.assert_not_called()


def test_client_rejects_other_feature_citations(monkeypatch):
    response_body = answered_response()
    response_body["citations"][0]["source"] = "student-3/expiry.md"
    monkeypatch.setattr(
        rag_client.urllib.request, "urlopen",
        Mock(return_value=FakeHTTPResponse(response_body)),
    )
    client = rag_client.PatientAdmissionRAGClient(True, "http://127.0.0.1:8100", 100)

    with pytest.raises(rag_client.RAGInvalidResponse):
        client.ask("How are admissions scheduled?")


def test_client_rejects_questions_outside_contract():
    client = rag_client.PatientAdmissionRAGClient(True, "http://127.0.0.1:8100", 100)

    with pytest.raises(rag_client.RAGInputError):
        client.ask(" ")
    with pytest.raises(rag_client.RAGInputError):
        client.ask("x" * 501)


def test_endpoint_returns_answer(monkeypatch):
    monkeypatch.setattr(rag_endpoints, "RAG_ENABLED", True)
    client = Mock()
    client.ask.return_value = {
        "status": "answered", "answer": "Admissions use 15-minute blocks [S1].",
        "confidence": "medium", "citations": [],
    }
    monkeypatch.setattr(rag_endpoints, "PatientAdmissionRAGClient", Mock(return_value=client))

    response = create_app().test_client().post(
        "/api/rag/ask", json={"question": "How are admissions scheduled?"},
        headers={"X-HOMS-Role": "Receptionist"},
    )

    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == "answered"
    client.ask.assert_called_once_with("How are admissions scheduled?")


def test_endpoint_reports_disabled_without_calling_server(monkeypatch):
    monkeypatch.setattr(rag_endpoints, "RAG_ENABLED", False)
    urlopen = Mock()
    monkeypatch.setattr(rag_client.urllib.request, "urlopen", urlopen)

    response = create_app().test_client().post(
        "/api/rag/ask", json={"question": "Admission rules?"},
        headers={"X-HOMS-Role": "Receptionist"},
    )

    assert response.status_code == 503
    assert response.get_json()["data"] == {"error": "rag_disabled"}
    urlopen.assert_not_called()


def test_endpoint_rejects_extra_fields_and_roles(monkeypatch):
    monkeypatch.setattr(rag_endpoints, "RAG_ENABLED", True)
    test_client = create_app().test_client()

    extra_field = test_client.post(
        "/api/rag/ask", json={"question": "Admission rules?", "feature": "student-3"},
        headers={"X-HOMS-Role": "Receptionist"},
    )
    wrong_role = test_client.post(
        "/api/rag/ask", json={"question": "Admission rules?"},
        headers={"X-HOMS-Role": "Doctor"},
    )

    assert extra_field.status_code == 400
    assert wrong_role.status_code == 403