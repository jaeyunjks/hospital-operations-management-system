"""Student 5 API boundary tests for the shared MCP integration."""

from __future__ import annotations

import pytest

from services import mcp_client


def envelope(ward=None):
    row = {
        "ward": ward or "Emergency", "total_beds": 10, "occupied": 6,
        "available": 2, "reserved": 1, "maintenance": 1,
        "monitored_beds": 10, "occupancy_pct": 60.0,
        "care_categories": ["Short-term"],
    }
    return {
        "schema_version": "1.0", "ok": True,
        "tool": mcp_client.TOOL_NAME,
        "data": {
            "requested_ward": ward, "wards": [row],
            "totals": {key: row[key] for key in (*mcp_client.COUNT_FIELDS,
                                                   "occupancy_pct")},
            "source": mcp_client.SOURCE,
        },
        "error": None,
    }


def install_adapter(monkeypatch, *, result=None, error=None, calls=None):
    calls = calls if calls is not None else []

    class FakeAdapter:
        def __init__(self, **configuration):
            calls.append(("config", configuration))

        def get_ward_occupancy(self, ward=None):
            calls.append(("call", ward))
            if error:
                raise error
            return result or envelope(ward)

    import routes.mcp_routes as routes
    monkeypatch.setattr(routes, "WardOccupancyMCPClient", FakeAdapter)
    return calls


def enable(client):
    client.application.config.update(
        MCP_ENABLED=True,
        MCP_SERVER_URL="http://127.0.0.1:8000/mcp",
        MCP_TIMEOUT=15.0,
    )


def test_mcp_is_disabled_by_default(client, monkeypatch):
    client.application.config["MCP_ENABLED"] = False
    response = client.get("/api/mcp/ward-occupancy")
    assert response.status_code == 503
    assert response.json == {
        "error": "mcp_unavailable",
        "message": "Ward occupancy via MCP is not enabled.",
    }


def test_all_wards_and_exact_ward(client, monkeypatch):
    enable(client)
    calls = install_adapter(monkeypatch)
    all_response = client.get("/api/mcp/ward-occupancy")
    exact_response = client.get("/api/mcp/ward-occupancy?ward=Emergency")
    assert all_response.status_code == exact_response.status_code == 200
    assert all_response.json["data"]["source"] == mcp_client.SOURCE
    assert exact_response.json["data"]["requested_ward"] == "Emergency"
    assert ("call", None) in calls
    assert ("call", "Emergency") in calls


def test_manager_guard_runs_before_mcp(client, monkeypatch):
    enable(client)
    calls = install_adapter(monkeypatch)
    response = client.get(
        "/api/mcp/ward-occupancy", headers={"X-HOMS-Role": "Employee"}
    )
    assert response.status_code == 403
    assert response.json["error"] == "forbidden"
    assert calls == []


@pytest.mark.parametrize(
    "query",
    ["ward=", "ward=%20%20", f"ward={'x' * 201}",
     "ward=Emergency&ward=Surgery", "department=Emergency"],
)
def test_invalid_query_is_rejected_before_mcp(client, monkeypatch, query):
    enable(client)
    calls = install_adapter(monkeypatch)
    response = client.get(f"/api/mcp/ward-occupancy?{query}")
    assert response.status_code == 400
    assert response.json["error"] == "validation_error"
    assert calls == []


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (mcp_client.MCPToolFailure("WARD_NOT_FOUND"), 404, "not_found"),
        (mcp_client.MCPUnavailable(), 503, "mcp_unavailable"),
        (mcp_client.MCPToolFailure("upstream_unavailable"), 503, "mcp_unavailable"),
        (mcp_client.MCPTimeout(), 504, "mcp_timeout"),
        (mcp_client.MCPToolFailure("upstream_timeout"), 504, "mcp_timeout"),
        (mcp_client.MCPInvalidResponse(), 502, "mcp_invalid_response"),
    ],
)
def test_adapter_failures_are_safely_mapped(
        client, monkeypatch, failure, status, code):
    enable(client)
    install_adapter(monkeypatch, error=failure)
    response = client.get("/api/mcp/ward-occupancy")
    assert response.status_code == status
    assert response.json["error"] == code
    assert "Traceback" not in response.get_data(as_text=True)


def test_r0_api_and_health_are_unaffected_when_mcp_disabled(client):
    client.application.config["MCP_ENABLED"] = False
    assert client.get("/api").status_code == 200
    assert client.get("/api/shifts/coverage").status_code == 200
    health = client.get("/health")
    assert health.status_code == 200
    assert "mcp" not in health.json

