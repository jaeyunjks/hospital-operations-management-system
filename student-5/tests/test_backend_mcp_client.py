"""Contract and lifecycle tests for Student 5's narrow MCP adapter."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from services import mcp_client


def success(ward=None):
    name = ward or "Emergency"
    row = {
        "ward": name, "total_beds": 8, "occupied": 5, "available": 2,
        "reserved": 1, "maintenance": 0, "monitored_beds": 8,
        "occupancy_pct": 62.5, "care_categories": ["Short-term"],
    }
    return {
        "schema_version": "1.0", "ok": True, "tool": mcp_client.TOOL_NAME,
        "data": {
            "requested_ward": ward, "wards": [row],
            "totals": {key: row[key] for key in (*mcp_client.COUNT_FIELDS,
                                                   "occupancy_pct")},
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

    async def __aexit__(self, *args):
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
        is_error=False, structured_content=success())
    FakeSDKClient.enter_error = None
    FakeSDKClient.delay = 0
    monkeypatch.setattr(mcp_client, "Client", FakeSDKClient)


def adapter(**kwargs):
    return mcp_client.WardOccupancyMCPClient(
        enabled=True, server_url="http://127.0.0.1:8000/mcp", timeout=1,
        **kwargs,
    )


def test_uses_configured_url_fixed_tool_and_omits_ward():
    result = adapter().get_ward_occupancy()
    sdk = FakeSDKClient.instances[0]
    assert sdk.server == "http://127.0.0.1:8000/mcp"
    assert sdk.calls[0][0:2] == (mcp_client.TOOL_NAME, {})
    assert sdk.kwargs["read_timeout_seconds"] == 1
    assert result == success()
    assert sdk.closed is True


def test_sends_exact_ward_only():
    FakeSDKClient.result = SimpleNamespace(
        is_error=False, structured_content=success("Emergency"))
    adapter().get_ward_occupancy("Emergency")
    assert FakeSDKClient.instances[0].calls[0][1] == {"ward": "Emergency"}


def test_public_operation_accepts_no_tool_or_server_override():
    import inspect
    assert list(inspect.signature(
        mcp_client.WardOccupancyMCPClient.get_ward_occupancy
    ).parameters) == ["self", "ward"]


def test_disabled_never_opens_a_session():
    client = mcp_client.WardOccupancyMCPClient(enabled=False)
    with pytest.raises(mcp_client.MCPDisabledError):
        client.get_ward_occupancy()
    assert FakeSDKClient.instances == []


def test_tool_error_is_classified_and_session_closes():
    failed = {
        "schema_version": "1.0", "ok": False, "tool": mcp_client.TOOL_NAME,
        "data": None,
        "error": {"code": "WARD_NOT_FOUND", "message": "Not found", "details": {}},
    }
    FakeSDKClient.result = SimpleNamespace(is_error=True, structured_content=failed)
    with pytest.raises(mcp_client.MCPToolFailure) as caught:
        adapter().get_ward_occupancy("Missing")
    assert caught.value.code == "WARD_NOT_FOUND"
    assert FakeSDKClient.instances[0].closed is True


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body.update(schema_version="2.0"),
        lambda body: body.update(tool="another_tool"),
        lambda body: body.update(ok=False),
        lambda body: body.update(error={"code": "x"}),
        lambda body: body["data"].pop("source"),
        lambda body: body["data"]["wards"][0].pop("available"),
        lambda body: body["data"]["wards"][0].update(patient_name="Private"),
        lambda body: body["data"]["totals"].update(total_beds=99),
    ],
)
def test_malformed_contract_is_rejected(change):
    body = copy.deepcopy(success())
    change(body)
    FakeSDKClient.result = SimpleNamespace(is_error=False, structured_content=body)
    with pytest.raises(mcp_client.MCPInvalidResponse):
        adapter().get_ward_occupancy()
    assert FakeSDKClient.instances[0].closed is True


def test_unavailable_server_is_classified():
    FakeSDKClient.enter_error = OSError("private transport detail")
    with pytest.raises(mcp_client.MCPUnavailable):
        adapter().get_ward_occupancy()


def test_timeout_is_bounded_and_session_closes():
    FakeSDKClient.delay = 0.05
    slow = mcp_client.WardOccupancyMCPClient(
        enabled=True, server_url="http://127.0.0.1:8000/mcp", timeout=0.001)
    with pytest.raises(mcp_client.MCPTimeout):
        slow.get_ward_occupancy()
    assert FakeSDKClient.instances[0].closed is True


@pytest.mark.parametrize("url", ["", "ftp://localhost/mcp", "http://u:p@host/mcp",
                                  "http://host/mcp?tool=other"])
def test_invalid_config_is_a_contract_failure(url):
    invalid = mcp_client.WardOccupancyMCPClient(
        enabled=True, server_url=url, timeout=1)
    with pytest.raises(mcp_client.MCPInvalidResponse):
        invalid.get_ward_occupancy()
    assert FakeSDKClient.instances == []

