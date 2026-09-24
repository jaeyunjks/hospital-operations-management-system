#!/usr/bin/env python3
"""Shared, non-containerised HOMS MCP server.

Run from the repository root with::

    python3 ai-services/mcp-server/server.py
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any, Dict
from urllib.parse import urlsplit

import uvicorn
from mcp.server import Server, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

from tools.system import MAX_MESSAGE_LENGTH, TOOL_NAME, homs_echo, validation_error
from tools import pharmacy_stock, ward_occupancy

SERVER_NAME = "HOMS Shared MCP Server"
SERVER_VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_PATH = "/mcp"
DEFAULT_STUDENT4_API_URL = "http://127.0.0.1:5400/api"
DEFAULT_STUDENT4_API_TIMEOUT = 10.0
DEFAULT_STUDENT3_API_URL = "http://127.0.0.1:5300/api"
DEFAULT_STUDENT3_API_TIMEOUT = 10.0
WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]", "*"}


@dataclass(frozen=True)
class ServerConfig:
    """Validated environment-controlled network configuration."""

    host: str
    port: int
    path: str
    allow_docker_host: bool
    student4_api_url: str
    student4_api_timeout: float
    student3_api_url: str
    student3_api_timeout: float


def _api_url(name: str, default: str) -> str:
    """Read one operator-configured upstream API base URL."""

    api_url = os.environ.get(name, default).strip().rstrip("/")
    try:
        parsed = urlsplit(api_url)
        valid_url = (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            valid_url = False
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError(
            f"{name} must be an HTTP(S) API base URL without credentials, query or fragment"
        )
    return api_url


def _api_timeout(name: str, default: float) -> float:
    """Read one bounded upstream deadline in seconds."""

    try:
        api_timeout = float(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(api_timeout) or not 0.1 <= api_timeout <= 30:
        raise ValueError(f"{name} must be between 0.1 and 30 seconds")
    return api_timeout


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

    raw_allow_docker = os.environ.get(
        "HOMS_MCP_ALLOW_DOCKER_HOST", "false"
    ).strip().lower()
    if raw_allow_docker not in {"true", "false"}:
        raise ValueError("HOMS_MCP_ALLOW_DOCKER_HOST must be 'true' or 'false'")
    allow_docker_host = raw_allow_docker == "true"
    if host in WILDCARD_HOSTS and not allow_docker_host:
        raise ValueError(
            "HOMS_MCP_ALLOW_DOCKER_HOST must be true when HOMS_MCP_HOST uses a wildcard bind"
        )

    path = os.environ.get("HOMS_MCP_PATH", DEFAULT_PATH).strip()
    if not path.startswith("/") or path == "/":
        raise ValueError("HOMS_MCP_PATH must start with '/' and name an endpoint")

    return ServerConfig(
        host=host,
        port=port,
        path=path.rstrip("/"),
        allow_docker_host=allow_docker_host,
        student4_api_url=_api_url("HOMS_STUDENT4_API_URL", DEFAULT_STUDENT4_API_URL),
        student4_api_timeout=_api_timeout(
            "HOMS_STUDENT4_API_TIMEOUT", DEFAULT_STUDENT4_API_TIMEOUT
        ),
        student3_api_url=_api_url("HOMS_STUDENT3_API_URL", DEFAULT_STUDENT3_API_URL),
        student3_api_timeout=_api_timeout(
            "HOMS_STUDENT3_API_TIMEOUT", DEFAULT_STUDENT3_API_TIMEOUT
        ),
    )


def build_transport_security(config: ServerConfig) -> TransportSecuritySettings:
    """Build the exact Host/Origin policy for this configured listener."""

    allowed_hosts = [
        f"127.0.0.1:{config.port}",
        f"localhost:{config.port}",
        f"[::1]:{config.port}",
    ]
    if config.allow_docker_host:
        allowed_hosts.append(f"host.docker.internal:{config.port}")

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=[
            f"http://127.0.0.1:{config.port}",
            f"http://localhost:{config.port}",
            f"http://[::1]:{config.port}",
        ],
    )


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


HOMS_WARD_OCCUPANCY_TOOL = Tool(
    name=ward_occupancy.TOOL_NAME,
    title="HOMS Ward Occupancy Status",
    description=(
        "Read the current privacy-safe ward occupancy snapshot from Student 4's "
        "published backend API. Optional ward is an exact canonical name, not a "
        "Student 5 department. Does not assign beds, infer staffing or forecast occupancy."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ward": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": ward_occupancy.MAX_WARD_LENGTH,
                "description": "Exact Student 4 ward name; omitted/null returns all wards.",
            }
        },
        "additionalProperties": False,
    },
    output_schema=ward_occupancy.OUTPUT_SCHEMA,
)


HOMS_PHARMACY_STOCK_TOOL = Tool(
    name=pharmacy_stock.TOOL_NAME,
    title="HOMS Pharmacy Stock Alerts",
    description=(
        "Read current pharmacy stock alerts from Student 3's published backend API: "
        "alert counts, medicines at or below their reorder level, and batches "
        "expiring within 30 days (each list capped at 10 rows). Read-only; does "
        "not issue, order, receive or write off stock."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "alert_type": {
                "type": ["string", "null"],
                "enum": [*pharmacy_stock.ALERT_TYPES, None],
                "description": (
                    "'low_stock', 'expiring_soon' or 'all'; omitted/null returns all. "
                    "Counts are always returned; lists not requested are null."
                ),
            }
        },
        "additionalProperties": False,
    },
    output_schema=pharmacy_stock.OUTPUT_SCHEMA,
)


async def list_tools(
    ctx: ServerRequestContext,
    params: PaginatedRequestParams | None,
) -> ListToolsResult:
    """Advertise the connectivity and controlled cross-service read tools."""

    return ListToolsResult(
        tools=[HOMS_ECHO_TOOL, HOMS_WARD_OCCUPANCY_TOOL, HOMS_PHARMACY_STOCK_TOOL]
    )


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

    if params.name == ward_occupancy.TOOL_NAME:
        arguments = params.arguments or {}
        unexpected = sorted(set(arguments) - {"ward"})
        if unexpected:
            return _result(
                ward_occupancy.tool_error(
                    "validation_error",
                    "Only the optional 'ward' argument is accepted.",
                    {"reason": "unexpected_fields", "fields": unexpected},
                )
            )
        return _result(
            await ward_occupancy.homs_ward_occupancy_status(
                arguments.get("ward"),
                api_url=config.student4_api_url,
                timeout=config.student4_api_timeout,
            )
        )

    if params.name == pharmacy_stock.TOOL_NAME:
        arguments = params.arguments or {}
        unexpected = sorted(set(arguments) - {"alert_type"})
        if unexpected:
            return _result(
                pharmacy_stock.tool_error(
                    "validation_error",
                    "Only the optional 'alert_type' argument is accepted.",
                    {"reason": "unexpected_fields", "fields": unexpected},
                )
            )
        return _result(
            await pharmacy_stock.homs_pharmacy_stock_alerts(
                arguments.get("alert_type"),
                api_url=config.student3_api_url,
                timeout=config.student3_api_timeout,
            )
        )

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
transport_security = build_transport_security(config)
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
    transport_security=transport_security,
)


def main() -> None:
    """Run one local Streamable HTTP server process."""

    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
