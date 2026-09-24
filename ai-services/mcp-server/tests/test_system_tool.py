from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

from mcp import Client


MCP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_ROOT))

MODULE_PATH = MCP_ROOT / "server.py"
SPEC = importlib.util.spec_from_file_location("homs_mcp_server", MODULE_PATH)
assert SPEC and SPEC.loader
server_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server_module
SPEC.loader.exec_module(server_module)


def call_homs_echo(arguments):
    async def call():
        async with Client(server_module.mcp_server) as client:
            return await client.call_tool("homs_echo", arguments)

    return asyncio.run(call())


def assert_validation_error(result, reason):
    assert result.is_error is True
    assert result.structured_content["ok"] is False
    assert result.structured_content["data"] is None
    assert result.structured_content["error"]["code"] == "validation_error"
    assert result.structured_content["error"]["details"]["reason"] == reason


def test_registers_echo_ward_occupancy_and_pharmacy_stock():
    async def discover():
        async with Client(server_module.mcp_server) as client:
            return await client.list_tools()

    listing = asyncio.run(discover())

    assert [tool.name for tool in listing.tools] == [
        "homs_echo",
        "homs_ward_occupancy_status",
        "homs_pharmacy_stock_alerts",
    ]
    assert listing.tools[0].input_schema["required"] == ["message"]
    assert listing.tools[0].input_schema["properties"]["message"]["maxLength"] == 200
    for tool in listing.tools[1:]:
        assert tool.input_schema["additionalProperties"] is False
        assert "required" not in tool.input_schema


def test_valid_request_returns_structured_success():
    result = call_homs_echo({"message": "MCP connected"})

    assert result.is_error is False
    assert result.structured_content == {
        "schema_version": "1.0",
        "ok": True,
        "tool": "homs_echo",
        "data": {"message": "MCP connected"},
        "error": None,
    }


def test_blank_input_returns_structured_error():
    assert_validation_error(call_homs_echo({"message": "   "}), "blank")


def test_missing_input_returns_structured_error():
    assert_validation_error(call_homs_echo({}), "required")


def test_wrong_type_returns_structured_error():
    result = call_homs_echo({"message": 42})

    assert_validation_error(result, "wrong_type")
    assert result.structured_content["error"]["details"]["received_type"] == "int"


def test_oversized_input_returns_structured_error():
    result = call_homs_echo({"message": "x" * 201})

    assert_validation_error(result, "too_long")
    assert result.structured_content["error"]["details"]["max_length"] == 200
    assert result.structured_content["error"]["details"]["received_length"] == 201


def test_output_is_deterministic():
    first = call_homs_echo({"message": "same input"})
    second = call_homs_echo({"message": "same input"})

    assert first.structured_content == second.structured_content
    assert first.content == second.content
