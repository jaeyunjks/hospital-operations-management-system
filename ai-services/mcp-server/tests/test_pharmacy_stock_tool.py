from __future__ import annotations

import asyncio
import copy
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from jsonschema import validate
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as server_module
from tools import pharmacy_stock


def dashboard():
    """A Student 3 /api/dashboard/summary body, including fields MCP must drop."""

    return {
        "counts": {
            "active_medicines": 12,
            "low_stock": 2,
            "expiring_within_30_days": 2,
            "expiring_within_7_days": 1,
            "expired_batches": 1,
            "pending_approvals": 1,
        },
        "low_stock": [
            {
                "name": "Amoxicillin 500mg",
                "stock_quantity": 20,
                "reorder_level": 100,
                "supplier_name": "MedSupply Co",
            },
            {
                "name": "Insulin Glargine",
                "stock_quantity": 5,
                "reorder_level": 5,
                "supplier_name": None,
            },
        ],
        "expiring_soon": [
            {
                "medicine_name": "Paracetamol 500mg",
                "batch_number": "PCM-2026-01",
                "expiry_date": "2026-09-27",
                "quantity_remaining": 40,
                "days_until_expiry": 4,
            },
            {
                "medicine_name": "Salbutamol Inhaler",
                "batch_number": "SAL-0042",
                "expiry_date": "2026-10-15",
                "quantity_remaining": 12,
                "days_until_expiry": 22,
            },
        ],
        "recent_movements": [
            {
                "medicine_name": "Paracetamol 500mg",
                "movement_type": "issue",
                "quantity": 10,
                "performed_by": "Alex Staff",
                "created_at": "2026-09-22T10:00:00",
            }
        ],
        "agent": {"enabled": True, "last_run": None},
    }


@pytest.fixture
def mock_api(monkeypatch):
    """Mock the HTTP boundary, not the projection or MCP dispatcher."""

    def install(body=None, *, status=200, error=None, text=None):
        requests = []

        def respond(request):
            requests.append(request)
            if error:
                raise error
            if text is not None:
                return httpx.Response(status, text=text)
            return httpx.Response(status, json=dashboard() if body is None else body)

        transport = httpx.MockTransport(respond)
        monkeypatch.setattr(
            pharmacy_stock,
            "_client",
            lambda timeout: httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ),
        )
        return requests

    return install


def call(arguments):
    async def invoke():
        async with Client(server_module.mcp_server) as client:
            return await client.call_tool(pharmacy_stock.TOOL_NAME, arguments)

    result = asyncio.run(invoke())
    validate(result.structured_content, pharmacy_stock.OUTPUT_SCHEMA)
    return result


def assert_error(result, code, reason=None):
    assert result.is_error is True
    assert result.structured_content["tool"] == "homs_pharmacy_stock_alerts"
    assert result.structured_content["ok"] is False
    assert result.structured_content["data"] is None
    assert result.structured_content["error"]["code"] == code
    if reason:
        assert result.structured_content["error"]["details"]["reason"] == reason


@pytest.mark.parametrize("arguments", [{}, {"alert_type": None}, {"alert_type": "all"}])
def test_all_alerts(arguments, mock_api):
    requests = mock_api()
    result = call(arguments)
    assert result.is_error is False
    body = dashboard()
    assert result.structured_content == {
        "schema_version": "1.0",
        "ok": True,
        "tool": "homs_pharmacy_stock_alerts",
        "data": {
            "alert_type": "all",
            "counts": body["counts"],
            "low_stock": body["low_stock"],
            "expiring_soon": body["expiring_soon"],
            "source": "student-3-pharmacy-api",
        },
        "error": None,
    }
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/dashboard/summary"
    assert not requests[0].url.query


def test_staff_names_and_agent_state_are_not_exposed(mock_api):
    mock_api()
    result = call({})
    text = result.content[0].text
    assert "Alex Staff" not in text
    assert "recent_movements" not in result.structured_content["data"]
    assert "agent" not in result.structured_content["data"]


@pytest.mark.parametrize(
    "alert_type,low_stock_present,expiring_present",
    [("low_stock", True, False), ("expiring_soon", False, True)],
)
def test_alert_type_filters_lists_but_keeps_counts(
    alert_type, low_stock_present, expiring_present, mock_api
):
    mock_api()
    data = call({"alert_type": alert_type}).structured_content["data"]
    assert data["alert_type"] == alert_type
    assert data["counts"] == dashboard()["counts"]
    assert (data["low_stock"] is not None) is low_stock_present
    assert (data["expiring_soon"] is not None) is expiring_present


def test_empty_alerts_are_valid(mock_api):
    body = dashboard()
    body["counts"].update(low_stock=0, expiring_within_30_days=0, expiring_within_7_days=0)
    body["low_stock"] = []
    body["expiring_soon"] = []
    mock_api(body)
    result = call({})
    assert result.is_error is False
    assert result.structured_content["data"]["low_stock"] == []
    assert result.structured_content["data"]["expiring_soon"] == []


def test_configured_url(mock_api, monkeypatch):
    requests = mock_api()
    monkeypatch.setattr(
        server_module,
        "config",
        replace(
            server_module.config, student3_api_url="http://student3.example:5300/api"
        ),
    )
    assert call({}).is_error is False
    assert str(requests[0].url) == "http://student3.example:5300/api/dashboard/summary"


@pytest.mark.parametrize(
    "alert_type,reason",
    [
        ("", "not_allowed"),
        ("LOW_STOCK", "not_allowed"),
        ("expired", "not_allowed"),
        (42, "wrong_type"),
        (False, "wrong_type"),
        ([], "wrong_type"),
    ],
)
def test_input_validation_does_not_call_student3(alert_type, reason, mock_api):
    requests = mock_api()
    assert_error(call({"alert_type": alert_type}), "validation_error", reason)
    assert requests == []


def test_unexpected_fields(mock_api):
    requests = mock_api()
    result = call({"alert_type": "all", "medicine_id": 1})
    assert_error(result, "validation_error", "unexpected_fields")
    assert result.structured_content["error"]["details"]["fields"] == ["medicine_id"]
    assert requests == []


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"error": httpx.ConnectError("refused")}, "upstream_unavailable"),
        ({"error": httpx.ReadTimeout("slow")}, "upstream_timeout"),
        ({"status": 503, "body": {"error": "Database service unavailable"}}, "upstream_unavailable"),
        ({"status": 504, "body": {}}, "upstream_timeout"),
        ({"status": 404, "body": {}}, "upstream_invalid_response"),
        ({"status": 302, "body": {}}, "upstream_invalid_response"),
        ({"text": "<html>not json</html>"}, "upstream_invalid_response"),
    ],
)
def test_upstream_failures_are_structured(kwargs, code, mock_api):
    mock_api(kwargs.pop("body", None), **kwargs)
    result = call({})
    assert_error(result, code)
    assert "refused" not in result.content[0].text
    assert "Database service" not in result.content[0].text


def _mutate(path, value):
    body = dashboard()
    target = body
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return body


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"counts": []},
        _mutate(("counts", "low_stock"), -1),
        _mutate(("counts", "low_stock"), True),
        _mutate(("counts", "expired_batches"), KeyError),
        _mutate(("low_stock",), KeyError),
        _mutate(("low_stock", 0, "stock_quantity"), 150),
        _mutate(("low_stock", 0, "stock_quantity"), 2.5),
        _mutate(("low_stock", 0, "name"), " "),
        _mutate(("low_stock", 0, "supplier_name"), 7),
        _mutate(("expiring_soon", 0, "days_until_expiry"), -1),
        _mutate(("expiring_soon", 0, "days_until_expiry"), 31),
        _mutate(("expiring_soon", 0, "expiry_date"), "27/09/2026"),
        _mutate(("expiring_soon", 0, "quantity_remaining"), None),
        _mutate(("counts", "low_stock"), 1),
        _mutate(("counts", "expiring_within_7_days"), 0),
        _mutate(("counts", "expiring_within_7_days"), 5),
        _mutate(("counts", "active_medicines"), 1),
    ],
)
def test_malformed_or_inconsistent_dashboard_is_rejected(body, mock_api):
    mock_api(copy.deepcopy(body))
    assert_error(call({}), "upstream_invalid_response")


def test_output_is_deterministic(mock_api):
    mock_api()
    first, second = call({}), call({})
    assert first.content[0].text == second.content[0].text
    assert first.structured_content == second.structured_content
