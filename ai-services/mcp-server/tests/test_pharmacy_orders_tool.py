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
from tools import pharmacy_orders


def order(po_id, status, *, ai=False, days_overdue=0, expected="2026-10-10"):
    return {"po_id": po_id, "medicine_name": f"Medicine {po_id}", "supplier_name": "MedSupply Australia",
            "status": status, "quantity_ordered": 100, "outstanding": 100, "total_value": 250.5,
            "ai_generated": ai, "created_at": "2026-09-20", "expected_at": expected, "days_overdue": days_overdue}


def alerts():
    """A Student 3 /api/purchase-orders/alerts body."""

    return {
        "counts": {"pending_approval": 2, "ai_suggested_pending": 1, "approved": 1, "ordered": 1, "overdue": 1},
        "pending_approval": [order(1, "pending_approval", ai=True, expected=None), order(2, "pending_approval")],
        "approved": [order(3, "approved")],
        "ordered": [order(4, "ordered", days_overdue=5, expected="2026-09-26")],
        "overdue": [order(4, "ordered", days_overdue=5, expected="2026-09-26")],
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
            return httpx.Response(status, json=alerts() if body is None else body)

        transport = httpx.MockTransport(respond)
        monkeypatch.setattr(
            pharmacy_orders, "_client",
            lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout,
                                              follow_redirects=False, trust_env=False))
        return requests

    return install


def call(arguments):
    async def invoke():
        async with Client(server_module.mcp_server) as client:
            return await client.call_tool(pharmacy_orders.TOOL_NAME, arguments)

    result = asyncio.run(invoke())
    validate(result.structured_content, pharmacy_orders.OUTPUT_SCHEMA)
    return result


def assert_error(result, code, reason=None):
    assert result.is_error is True
    assert result.structured_content["tool"] == "homs_pharmacy_order_alerts"
    assert result.structured_content["data"] is None
    assert result.structured_content["error"]["code"] == code
    if reason:
        assert result.structured_content["error"]["details"]["reason"] == reason


@pytest.mark.parametrize("arguments", [{}, {"alert_type": None}, {"alert_type": "all"}])
def test_all_alerts(arguments, mock_api):
    requests = mock_api()
    result = call(arguments)
    assert result.is_error is False
    body = alerts()
    assert result.structured_content["data"] == {
        "alert_type": "all", "counts": body["counts"],
        "pending_approval": body["pending_approval"], "approved": body["approved"],
        "ordered": body["ordered"], "overdue": body["overdue"], "source": "student-3-pharmacy-api",
    }
    assert [(r.method, r.url.path, r.url.query) for r in requests] == [("GET", "/api/purchase-orders/alerts", b"")]


@pytest.mark.parametrize("alert_type,kept", [("pending_approval", {"pending_approval"}), ("overdue", {"overdue"})])
def test_alert_type_filters_lists_but_keeps_counts(alert_type, kept, mock_api):
    mock_api()
    data = call({"alert_type": alert_type}).structured_content["data"]
    assert data["counts"] == alerts()["counts"]
    for name in ("pending_approval", "approved", "ordered", "overdue"):
        assert (data[name] is not None) is (name in kept)


def test_extra_upstream_fields_are_dropped(mock_api):
    body = alerts()
    body["pending_approval"][0].update(created_by="Olivia Martin", ai_reasoning="internal note")
    mock_api(body)
    text = call({}).content[0].text
    assert "Olivia Martin" not in text and "internal note" not in text


def test_configured_url(mock_api, monkeypatch):
    requests = mock_api()
    monkeypatch.setattr(server_module, "config",
                        replace(server_module.config, student3_api_url="http://student3.example:5300/api"))
    assert call({}).is_error is False
    assert str(requests[0].url) == "http://student3.example:5300/api/purchase-orders/alerts"


@pytest.mark.parametrize("alert_type,reason", [
    ("", "not_allowed"), ("approved", "not_allowed"), ("OVERDUE", "not_allowed"),
    (1, "wrong_type"), (True, "wrong_type"), ([], "wrong_type"),
])
def test_input_validation_does_not_call_student3(alert_type, reason, mock_api):
    requests = mock_api()
    assert_error(call({"alert_type": alert_type}), "validation_error", reason)
    assert requests == []


def test_unexpected_fields(mock_api):
    requests = mock_api()
    result = call({"alert_type": "all", "po_id": 4})
    assert_error(result, "validation_error", "unexpected_fields")
    assert requests == []


@pytest.mark.parametrize("kwargs,code", [
    ({"error": httpx.ConnectError("refused")}, "upstream_unavailable"),
    ({"error": httpx.ReadTimeout("slow")}, "upstream_timeout"),
    ({"status": 503, "body": {}}, "upstream_unavailable"),
    ({"status": 504, "body": {}}, "upstream_timeout"),
    ({"status": 404, "body": {}}, "upstream_invalid_response"),
    ({"text": "<html>"}, "upstream_invalid_response"),
])
def test_upstream_failures_are_structured(kwargs, code, mock_api):
    mock_api(kwargs.pop("body", None), **kwargs)
    assert_error(call({}), code)


def _mutate(path, value):
    body = alerts()
    target = body
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return body


@pytest.mark.parametrize("body", [
    [],
    {"counts": []},
    _mutate(("counts", "approved"), -1),
    _mutate(("counts", "overdue"), KeyError),
    _mutate(("approved",), KeyError),
    _mutate(("pending_approval", 0, "status"), "approved"),
    _mutate(("overdue", 0, "days_overdue"), 0),
    _mutate(("overdue", 0, "status"), "pending_approval"),
    _mutate(("approved", 0, "po_id"), 0),
    _mutate(("approved", 0, "total_value"), "250"),
    _mutate(("approved", 0, "ai_generated"), 1),
    _mutate(("approved", 0, "expected_at"), "soon"),
    _mutate(("approved", 0, "medicine_name"), " "),
    _mutate(("counts", "pending_approval"), 1),
    _mutate(("counts", "approved"), 5),
    _mutate(("counts", "ai_suggested_pending"), 3),
    _mutate(("counts", "overdue"), 3),
])
def test_malformed_or_inconsistent_alerts_are_rejected(body, mock_api):
    mock_api(copy.deepcopy(body))
    assert_error(call({}), "upstream_invalid_response")


def test_lists_longer_than_ten_are_rejected(mock_api):
    body = alerts()
    body["approved"] = [order(10 + n, "approved") for n in range(11)]
    body["counts"]["approved"] = 11
    mock_api(body)
    assert_error(call({}), "upstream_invalid_response")
