#!/usr/bin/env python3
"""Shared, non-containerised HOMS MCP server.

Run from the repository root with::

    python3 ai-services/mcp-server/server.py
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict

import uvicorn
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from tools.system import MAX_MESSAGE_LENGTH, TOOL_NAME, homs_echo, validation_error


SERVER_NAME = "HOMS Shared MCP Server"
SERVER_VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_PATH = "/mcp"


@dataclass(frozen=True)
class ServerConfig:
    """Validated environment-controlled network configuration."""

    host: str
    port: int
    path: str


def load_config() -> ServerConfig:
    """Load and validate server configuration from the environment."""

    host = os.environ.get("HOMS_MCP_HOST", DEFAULT_HOST).strip()
    if not host:
        raise ValueError("HOMS_MCP_HOST must not be blank")

    raw_port = os.environ.get("HOMS_MCP_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError as error:
        raise ValueError("HOMS_MCP_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("HOMS_MCP_PORT must be between 1 and 65535")

    path = os.environ.get("HOMS_MCP_PATH", DEFAULT_PATH).strip()
    if not path.startswith("/") or path == "/":
        raise ValueError("HOMS_MCP_PATH must start with '/' and name an endpoint")

    return ServerConfig(host=host, port=port, path=path.rstrip("/"))


HOMS_ECHO_TOOL = Tool(
    name=TOOL_NAME,
    title="HOMS Echo",
    description=(
        "Echo one bounded string to validate MCP connectivity and the shared "
        "structured result contract. Performs no external I/O."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_MESSAGE_LENGTH,
                "description": "Text to return unchanged.",
            }
        },
        "required": ["message"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "schema_version": {"const": "1.0"},
            "ok": {"type": "boolean"},
            "tool": {"const": TOOL_NAME},
            "data": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ]
            },
            "error": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "message": {"type": "string"},
                            "details": {"type": "object"},
                        },
                        "required": ["code", "message", "details"],
                        "additionalProperties": False,
                    },
                    {"type": "null"},
                ]
            },
        },
        "required": ["schema_version", "ok", "tool", "data", "error"],
        "additionalProperties": False,
    },
)


async def list_tools(
    ctx: ServerRequestContext,
    params: PaginatedRequestParams | None,
) -> ListToolsResult:
    """Advertise exactly the neutral connectivity tool."""

    return ListToolsResult(tools=[HOMS_ECHO_TOOL])


def _result(payload: Dict[str, Any]) -> CallToolResult:
    """Render one payload for both model-readable and structured consumers."""

    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            )
        ],
        structured_content=payload,
        is_error=not payload["ok"],
    )


async def call_tool(
    ctx: ServerRequestContext,
    params: CallToolRequestParams,
) -> CallToolResult:
    """Dispatch a tool call and preserve the structured result contract."""

    if params.name != TOOL_NAME:
        return _result(
            validation_error(
                f"Unknown tool: {params.name}",
                {"reason": "unknown_tool", "received": params.name},
            )
        )

    arguments = params.arguments or {}
    unexpected = sorted(set(arguments) - {"message"})
    if unexpected:
        return _result(
            validation_error(
                "Only the 'message' argument is accepted.",
                {"reason": "unexpected_fields", "fields": unexpected},
            )
        )

    payload = homs_echo(arguments["message"]) if "message" in arguments else homs_echo()
    return _result(payload)


config = load_config()
mcp_server = Server(
    SERVER_NAME,
    version=SERVER_VERSION,
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)
app = mcp_server.streamable_http_app(
    host=config.host,
    streamable_http_path=config.path,
    stateless_http=True,
    json_response=True,
)


def main() -> None:
    """Run one local Streamable HTTP server process."""

    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
