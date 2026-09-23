"""Backend access to the shared MCP server: flag, boundary and failure mapping."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from services import mcp_client

HAS_MCP = importlib.util.find_spec("mcp") is not None


def fake_server():
    """A tiny in-process MCP server standing in for the shared one."""
    from mcp.server import Server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    async def list_tools(ctx, params):
        return ListToolsResult(tools=[
            Tool(name="homs_echo", input_schema={"type": "object"}),
            Tool(name="homs_ward_occupancy_status", input_schema={"type": "object"}),
            Tool(name="homs_pharmacy_stock_alerts", title="HOMS Pharmacy Stock Alerts",
                 input_schema={"type": "object"}),
        ])

    async def call_tool(ctx, params):
        ok = (params.arguments or {}).get("alert_type") != "bogus"
        payload = {"schema_version": "1.0", "ok": ok, "tool": params.name,
                   "data": {"counts": {"low_stock": 3}} if ok else None,
                   "error": None if ok else {"code": "validation_error", "message": "bad", "details": {}}}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))],
                              structured_content=payload, is_error=not ok)

    return Server("fake", on_list_tools=list_tools, on_call_tool=call_tool)


class MCPDisabledTests(unittest.TestCase):
    """CI runs with MCP disabled; nothing may contact an MCP server."""

    def setUp(self):
        self.client = app.app.test_client()
        patcher = patch.object(mcp_client, "MCP_ENABLED", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        contact = patch.object(mcp_client, "_with_client", side_effect=AssertionError("MCP contacted"))
        contact.start()
        self.addCleanup(contact.stop)

    def test_status_reports_disabled(self):
        response = self.client.get("/api/mcp/status")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["enabled"])
        self.assertFalse(body["reachable"])
        self.assertEqual(body["tools"], [])
        self.assertEqual(body["allowed_tools"], ["homs_pharmacy_stock_alerts", "homs_echo"])

    def test_call_returns_503_when_disabled(self):
        response = self.client.post("/api/mcp/call", json={"tool": "homs_pharmacy_stock_alerts"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["outcome"], "disabled")


class MCPRequestValidationTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        patcher = patch.object(mcp_client, "call_tool", side_effect=AssertionError("MCP called"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, body):
        return self.client.post("/api/mcp/call", json=body)

    def test_invalid_bodies_are_rejected_before_mcp(self):
        cases = [
            ([], 400),
            ({}, 400),
            ({"tool": " "}, 400),
            ({"tool": "homs_pharmacy_stock_alerts", "arguments": []}, 400),
            ({"tool": "homs_pharmacy_stock_alerts", "arguments": {"x": "y" * 3000}}, 400),
        ]
        for body, status in cases:
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.post(body).status_code, status)

    def test_other_features_tools_are_outside_the_boundary(self):
        response = self.post({"tool": "homs_ward_occupancy_status"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("not available to the pharmacy feature", response.get_json()["error"])


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed")
class MCPEnabledTests(unittest.TestCase):
    def setUp(self):
        from mcp import Client

        server = fake_server()
        self.client = app.app.test_client()

        async def in_process(operation):
            async with Client(server) as client:
                return await operation(client)

        for target, value in (("MCP_ENABLED", True), ("_with_client", in_process)):
            patcher = patch.object(mcp_client, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_status_lists_only_allowlisted_tools(self):
        body = self.client.get("/api/mcp/status").get_json()
        self.assertTrue(body["enabled"])
        self.assertTrue(body["reachable"])
        self.assertEqual([tool["name"] for tool in body["tools"]], ["homs_echo", "homs_pharmacy_stock_alerts"])

    def test_successful_call_returns_structured_tool_result(self):
        response = self.client.post("/api/mcp/call", json={
            "tool": "homs_pharmacy_stock_alerts", "arguments": {"alert_type": "low_stock"}})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["outcome"], "ok")
        self.assertEqual(body["arguments"], {"alert_type": "low_stock"})
        self.assertEqual(body["result"]["data"], {"counts": {"low_stock": 3}})

    def test_tool_error_is_a_valid_structured_result(self):
        response = self.client.post("/api/mcp/call", json={
            "tool": "homs_pharmacy_stock_alerts", "arguments": {"alert_type": "bogus"}})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["outcome"], "tool_error")
        self.assertEqual(body["result"]["error"]["code"], "validation_error")


@unittest.skipUnless(HAS_MCP, "mcp SDK not installed")
class MCPTransportFailureTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        patcher = patch.object(mcp_client, "MCP_ENABLED", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self):
        return self.client.post("/api/mcp/call", json={"tool": "homs_pharmacy_stock_alerts"})

    def test_unreachable_server_returns_502(self):
        with patch.object(mcp_client, "MCP_SERVER_URL", "http://127.0.0.1:9/mcp"):
            response = self.call()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "Shared MCP server is unavailable")

    def test_timeout_inside_exception_group_returns_504(self):
        async def slow(operation):
            raise ExceptionGroup("task group", [TimeoutError()])

        with patch.object(mcp_client, "_with_client", slow):
            response = self.call()
        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.get_json()["outcome"], "timeout")

    def test_invalid_server_url_is_not_contacted(self):
        with patch.object(mcp_client, "MCP_SERVER_URL", "file:///etc/passwd"), \
                patch.object(mcp_client, "_with_client", side_effect=AssertionError("contacted")):
            response = self.call()
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
