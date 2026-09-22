"""End-to-end: the grounded loop against the real HOMS MCP server in-process.

Only the Student 4 HTTP boundary and the model are replaced; MCP discovery,
dispatch, validation and the privacy projection run for real.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
httpx = pytest.importorskip("httpx")

AI_SERVICES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(AI_SERVICES / "mcp-server"))
import server as mcp_server_module  # noqa: E402
from tools import ward_occupancy  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "grounded_loop_e2e", AI_SERVICES / "agentic-loop" / "grounded_loop.py")
grounded = importlib.util.module_from_spec(SPEC)
sys.modules["grounded_loop_e2e"] = grounded
SPEC.loader.exec_module(grounded)


UPSTREAM = {
    "success": True,
    "error": None,
    "data": {
        "wards": [{"ward": "Emergency", "total_beds": 3, "occupied": 1, "available": 2,
                   "reserved": 0, "maintenance": 0, "monitored_beds": 3,
                   "occupancy_pct": 33.3, "care_categories": ["Short-term"],
                   "patient_name": "must never leak"}],
        "totals": {"total_beds": 3, "occupied": 1, "available": 2, "reserved": 0,
                   "maintenance": 0, "occupancy_pct": 33.3},
    },
}


@pytest.fixture
def student4_api(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=UPSTREAM))
    monkeypatch.setattr(
        ward_occupancy, "_client",
        lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout,
                                          follow_redirects=False, trust_env=False),
    )


class Chat:
    def __init__(self):
        self.seen = []

    def __call__(self, messages, tools):
        self.seen.append((messages, tools))
        if len(self.seen) == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "homs_ward_occupancy_status",
                              "arguments": {"ward": "Emergency"}}}]}
        return {"role": "assistant", "content": json.dumps({
            "answer": "Emergency has 2 of 3 beds available.",
            "cited_calls": ["call-1"], "limitations": "current snapshot only"})}


def test_loop_uses_real_mcp_server(student4_api):
    chat = Chat()
    loop = grounded.GroundedAgenticLoop(
        chat=chat, provider=grounded.MCPToolProvider(mcp_server_module.mcp_server),
        log=lambda _: None)
    result = asyncio.run(loop.run("How many Emergency beds are free?"))

    assert "homs_ward_occupancy_status" in result.tools
    call = result.calls[0]
    assert call.status == "executed" and call.ok is True
    assert call.result["data"]["wards"][0]["available"] == 2
    tool_message = chat.seen[1][0][-1]["content"]
    assert "must never leak" not in tool_message
    assert result.grounding["status"] == "grounded"
