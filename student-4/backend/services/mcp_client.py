"""Bounded client for the shared, non-containerised HOMS MCP server.

Only this module talks to MCP. Room & Bed may call only the tools in
``FEATURE_TOOLS``; the MCP server stays responsible for validating each
tool's arguments and for its own upstream access. Every failure becomes a
structured result, so a missing or slow MCP server degrades this feature
instead of breaking it.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import logging
import os
import time
from typing import Any
from urllib.parse import urlsplit

# Kept small and explicit: another feature's tools are not exposed here.
FEATURE_TOOLS = ("homs_ward_occupancy_status", "homs_echo")

MCP_ENABLED = os.environ.get("MCP_ENABLED", "false").strip().lower() == "true"
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp").strip()
try:
    MCP_TIMEOUT = float(os.environ.get("MCP_TIMEOUT", "15"))
except ValueError:
    MCP_TIMEOUT = 15.0

logger = logging.getLogger("student4.mcp")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@dataclass
class MCPResult:
    """A safe result for every MCP operation, including transport failures.

    ``outcome`` is ``ok`` (tool succeeded), ``tool_error`` (the tool returned
    a structured error), ``disabled``, ``timeout`` or ``unavailable``.
    """

    ok: bool
    outcome: str
    tool: str | None = None
    arguments: dict = field(default_factory=dict)
    result: Any = None
    tools: list | None = None
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _valid_server_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


def _flatten(error: BaseException) -> list:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for inner in error.exceptions for leaf in _flatten(inner)]
    return [error]


def _is_timeout(error: BaseException) -> bool:
    return any(isinstance(leaf, TimeoutError) or "Timeout" in type(leaf).__name__
               for leaf in _flatten(error))


async def _with_client(operation):
    from mcp import Client  # imported lazily so the backend starts without MCP installed

    async with asyncio.timeout(MCP_TIMEOUT):
        async with Client(MCP_SERVER_URL, read_timeout_seconds=MCP_TIMEOUT) as client:
            return await operation(client)


def _run(label: str, tool: str | None, arguments: dict, operation) -> MCPResult:
    """Run one short-lived MCP session and turn every failure into a result."""

    if not MCP_ENABLED:
        return MCPResult(False, "disabled", tool, arguments, error="MCP mode is disabled")
    if not _valid_server_url(MCP_SERVER_URL):
        return MCPResult(False, "unavailable", tool, arguments,
                         error="MCP_SERVER_URL is not a valid HTTP(S) URL")
    started = time.perf_counter()
    try:
        result = asyncio.run(_with_client(operation))
    except Exception as exc:  # the SDK raises ExceptionGroups of transport errors
        outcome = "timeout" if _is_timeout(exc) else "unavailable"
        result = MCPResult(False, outcome, tool, arguments,
                           error="Shared MCP server timed out" if outcome == "timeout"
                           else "Shared MCP server is unavailable")
    result.duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info("[MCP] %s tool=%s outcome=%s duration_ms=%s server=%s",
                label, tool or "-", result.outcome, result.duration_ms, MCP_SERVER_URL)
    return result


def list_feature_tools() -> MCPResult:
    """Discover the shared server's tools, filtered to this feature's allowlist."""

    async def operation(client):
        listing = await client.list_tools()
        tools = [{"name": tool.name, "title": tool.title, "description": tool.description,
                  "input_schema": tool.input_schema}
                 for tool in listing.tools if tool.name in FEATURE_TOOLS]
        return MCPResult(True, "ok", tools=tools)

    return _run("list_tools", None, {}, operation)


def call_tool(name: str, arguments: dict) -> MCPResult:
    """Call one allowlisted tool; callers must check ``FEATURE_TOOLS`` first."""

    if name not in FEATURE_TOOLS:
        raise ValueError("{} is not available to the room and bed feature".format(name))

    async def operation(client):
        response = await client.call_tool(name, arguments)
        payload = response.structured_content
        if payload is None:
            payload = {"content": [getattr(block, "text", None) for block in response.content or []]}
        return MCPResult(not response.is_error, "tool_error" if response.is_error else "ok",
                         name, arguments, result=payload)

    return _run("call_tool", name, arguments, operation)


def status() -> dict:
    """Non-crashing status for the UI and CI; only contacts MCP when enabled."""

    base = {"enabled": MCP_ENABLED, "server_url": MCP_SERVER_URL,
            "allowed_tools": list(FEATURE_TOOLS)}
    if not MCP_ENABLED:
        return {**base, "reachable": False, "tools": []}
    listing = list_feature_tools()
    return {**base, "reachable": listing.ok, "tools": listing.tools or [],
            **({"error": listing.error} if listing.error else {})}
