"""Frontend -> backend -> shared MCP / RAG boundary.

No shared server is started: the bounded clients are the seam, so the
routes are exercised against stubs. What matters here is that this feature
validates input, refuses tools outside its allowlist, keeps a disabled or
unreachable server from breaking the API, and passes an
insufficient-context answer through as a successful outcome.
"""

import importlib

import pytest

from conftest import data, error


@pytest.fixture()
def mcp(monkeypatch):
    module = importlib.import_module("services.mcp_client")
    importlib.reload(module)
    monkeypatch.setattr(module, "MCP_ENABLED", True)
    return module


@pytest.fixture()
def rag(monkeypatch):
    module = importlib.import_module("services.rag_client")
    importlib.reload(module)
    monkeypatch.setattr(module, "RAG_ENABLED", True)
    return module


def snapshot():
    return {
        "schema_version": "1.0", "ok": True, "tool": "homs_ward_occupancy_status",
        "data": {"requested_ward": "Emergency",
                 "wards": [{"ward": "Emergency", "total_beds": 3, "occupied": 1,
                            "available": 2, "reserved": 0, "maintenance": 0,
                            "monitored_beds": 3, "occupancy_pct": 33.3,
                            "care_categories": ["Short-term"]}],
                 "totals": {"total_beds": 3, "occupied": 1, "available": 2,
                            "reserved": 0, "maintenance": 0, "occupancy_pct": 33.3},
                 "source": "student-4-room-bed-api"},
        "error": None,
    }


def answered():
    return {
        "schema_version": "1.0", "status": "answered",
        "question": "Can the same bed hold two bookings at the same time?",
        "answer": "According to [S1], a bed can never hold two bookings at the same time.",
        "confidence": "high",
        "citations": [{"id": "S1", "source": "student-4/bed-allocation-and-conflicts.md",
                       "title": "Bed Allocation and Double-Booking Prevention",
                       "section": "The double-booking rule", "score": 0.82,
                       "snippet": "A bed can hold only one active arrangement."}],
        "reason": None,
        "retrieval": {"top_score": 0.82, "threshold": 0.62, "considered": 4, "relevant": 4},
        "models": {"embedding": "nomic-embed-text", "generation": "llama3.2:3b"},
    }


# --- MCP ------------------------------------------------------------------

def test_mcp_disabled_by_default(api):
    status = data(api.get("/api/mcp/status"))
    assert status["enabled"] is False
    assert status["reachable"] is False
    assert status["allowed_tools"] == ["homs_ward_occupancy_status", "homs_echo"]


def test_mcp_call_while_disabled_returns_503_not_a_crash(api):
    response = api.post("/api/mcp/call", json={"tool": "homs_ward_occupancy_status"})
    assert response.status_code == 503
    assert "disabled" in error(response).lower()


def test_tool_outside_the_allowlist_is_refused_without_calling_mcp(api, mcp, monkeypatch):
    called = []
    monkeypatch.setattr(mcp, "call_tool", lambda *a, **k: called.append(a))
    response = api.post("/api/mcp/call", json={"tool": "homs_pharmacy_stock_alerts"})
    assert response.status_code == 403
    assert called == []


def test_successful_tool_call_is_returned_unchanged(api, mcp, monkeypatch):
    monkeypatch.setattr(mcp, "call_tool", lambda name, arguments: mcp.MCPResult(
        True, "ok", name, arguments, result=snapshot()))
    body = data(api.post("/api/mcp/call", json={
        "tool": "homs_ward_occupancy_status", "arguments": {"ward": "Emergency"}}))
    assert body["result"]["data"]["wards"][0]["available"] == 2


def test_unreachable_mcp_server_maps_to_502(api, mcp, monkeypatch):
    monkeypatch.setattr(mcp, "call_tool", lambda name, arguments: mcp.MCPResult(
        False, "unavailable", name, arguments, error="Shared MCP server is unavailable"))
    response = api.post("/api/mcp/call", json={"tool": "homs_ward_occupancy_status"})
    assert response.status_code == 502


@pytest.mark.parametrize("payload", [{}, {"tool": ""}, {"tool": "homs_echo", "arguments": "no"}])
def test_mcp_call_validates_its_body(api, payload):
    assert api.post("/api/mcp/call", json=payload).status_code in (400, 403)


# --- RAG ------------------------------------------------------------------

def test_rag_disabled_by_default(api):
    status = data(api.get("/api/rag/status"))
    assert status["enabled"] is False
    assert status["feature"] == "student-4"
    assert status["documents"] == []


def test_rag_ask_while_disabled_returns_503(api):
    response = api.post("/api/rag/ask", json={"question": "Who can release a bed?"})
    assert response.status_code == 503


def test_answered_question_keeps_its_citations(api, rag, monkeypatch):
    monkeypatch.setattr(rag, "_request", lambda *a, **k: (200, answered()))
    body = data(api.post("/api/rag/ask", json={
        "question": "Can the same bed hold two bookings at the same time?"}))
    assert body["outcome"] == "answered"
    assert body["confidence"] == "high"
    assert body["citations"][0]["source"].startswith("student-4/")


def test_insufficient_context_is_a_successful_outcome(api, rag, monkeypatch):
    refusal = {"schema_version": "1.0", "status": "insufficient_context",
               "question": "What is the capital city of France?",
               "answer": "The knowledge base does not contain enough relevant information.",
               "confidence": "none", "citations": [], "reason": "no_relevant_context",
               "retrieval": {"top_score": 0.5, "threshold": 0.62, "considered": 4, "relevant": 0}}
    monkeypatch.setattr(rag, "_request", lambda *a, **k: (200, refusal))
    body = data(api.post("/api/rag/ask", json={"question": "What is the capital city of France?"}))
    assert body["outcome"] == "insufficient_context"
    assert body["citations"] == []


def test_citation_from_another_feature_is_rejected(api, rag, monkeypatch):
    leaked = answered()
    leaked["citations"][0]["source"] = "student-3/expiry-and-write-off.md"
    monkeypatch.setattr(rag, "_request", lambda *a, **k: (200, leaked))
    response = api.post("/api/rag/ask", json={"question": "Who can write off a batch?"})
    assert response.status_code == 502


@pytest.mark.parametrize("payload", [{}, {"question": "  "}, {"question": "x" * 501},
                                     {"question": "ok", "feature": "student-3"}])
def test_rag_ask_validates_its_body(api, payload):
    assert api.post("/api/rag/ask", json=payload).status_code == 400
