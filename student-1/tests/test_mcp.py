from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from backend.app import create_app
from backend.routes import mcp_endpoints
from backend.services import mcp_client


def success_envelope():
    ward = {
        "ward": "Emergency", "total_beds": 8, "occupied": 5,
        "available": 2, "reserved": 1, "maintenance": 0,
        "monitored_beds": 8, "occupancy_pct": 62.5,
        "care_categories": ["Short-term"],
    }
    return {
        "schema_version": "1.0", "ok": True,
        "tool": mcp_client.TOOL_NAME,
        "data": {
            "requested_ward": None,
            "wards": [ward],
            "totals": {field: ward[field] for field in (*mcp_client.COUNT_FIELDS, "occupancy_pct")},
            "source": mcp_client.SOURCE,
        },
        "error": None,
    }


class FakeSDKClient:
    instances = []
    result = None
    enter_error = None
    delay = 0

    def __init__(self, server, **kwargs):
        self.server = server
        self.kwargs = kwargs
        self.calls = []
        self.closed = False
        self.__class__.instances.append(self)

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    FakeSDKClient.instances = []
    FakeSDKClient.result = SimpleNamespace(
        is_error=False, structured_content=success_envelope())
    FakeSDKClient.enter_error = None
    FakeSDKClient.delay = 0
    monkeypatch.setattr(mcp_client, "Client", FakeSDKClient)


def adapter(**kwargs):
    return mcp_client.WardOccupancyMCPClient(
        enabled=True, server_url="http://127.0.0.1:8000/mcp", timeout=1,
        **kwargs,
    )


def test_client_calls_only_the_read_only_ward_tool():
    result = adapter().get_ward_occupancy()
    sdk = FakeSDKClient.instances[0]
    assert sdk.server == "http://127.0.0.1:8000/mcp"
    assert sdk.calls[0][0:2] == (mcp_client.TOOL_NAME, {})
    assert sdk.kwargs["read_timeout_seconds"] == 1
    assert result == success_envelope()
    assert sdk.closed


def test_disabled_client_does_not_open_mcp_session():
    client = mcp_client.WardOccupancyMCPClient(
        enabled=False, server_url="http://127.0.0.1:8000/mcp", timeout=1)
    with pytest.raises(mcp_client.MCPDisabledError):
        client.get_ward_occupancy()
    assert not FakeSDKClient.instances


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body.update(schema_version="2.0"),
        lambda body: body["data"]["wards"][0].update(patient_name="Private"),
        lambda body: body["data"]["totals"].update(total_beds=99),
        lambda body: body["data"]["wards"][0].update(occupied=True),
    ],
)
def test_client_rejects_malformed_or_unallowlisted_snapshot(change):
    body = copy.deepcopy(success_envelope())
    change(body)
    FakeSDKClient.result = SimpleNamespace(is_error=False, structured_content=body)
    with pytest.raises(mcp_client.MCPInvalidResponse):
        adapter().get_ward_occupancy()


def test_client_classifies_unavailable_server():
    FakeSDKClient.enter_error = OSError("private transport detail")
    with pytest.raises(mcp_client.MCPUnavailable):
        adapter().get_ward_occupancy()


def test_client_bounds_mcp_call_timeout():
    FakeSDKClient.delay = 0.05
    slow = mcp_client.WardOccupancyMCPClient(
        enabled=True, server_url="http://127.0.0.1:8000/mcp", timeout=0.001)
    with pytest.raises(mcp_client.MCPTimeout):
        slow.get_ward_occupancy()
    assert FakeSDKClient.instances[0].closed


def test_endpoint_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(mcp_endpoints, "MCP_ENABLED", False)
    response = create_app().test_client().get(
        "/api/mcp/ward-occupancy", headers={"X-HOMS-Role": "Receptionist"})
    assert response.status_code == 503
    assert response.json["data"] == {"error": "mcp_disabled"}
    assert not FakeSDKClient.instances


@pytest.mark.parametrize("role", ["Receptionist", "System Admin"])
def test_endpoint_returns_snapshot_to_reception_and_admin(monkeypatch, role):
    monkeypatch.setattr(mcp_endpoints, "MCP_ENABLED", True)
    response = create_app().test_client().get(
        "/api/mcp/ward-occupancy", headers={"X-HOMS-Role": role})
    assert response.status_code == 200
    assert response.json["data"] == success_envelope()


def test_endpoint_rejects_other_roles_before_mcp(monkeypatch):
    monkeypatch.setattr(mcp_endpoints, "MCP_ENABLED", True)
    response = create_app().test_client().get(
        "/api/mcp/ward-occupancy", headers={"X-HOMS-Role": "Doctor"})
    assert response.status_code == 403
    assert not FakeSDKClient.instances