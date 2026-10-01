"""Read-only MCP-backed operational context for Student 1."""

from flask import Blueprint

try:
    from backend.auth import ROLE_MANAGER, ROLE_RECEPTIONIST, require_role
    from backend.config import MCP_ENABLED, MCP_SERVER_URL, MCP_TIMEOUT
    from backend.responses import ok
    from backend.services.mcp_client import (
        MCPDisabledError,
        MCPInvalidResponse,
        MCPTimeout,
        MCPToolFailure,
        MCPUnavailable,
        WardOccupancyMCPClient,
    )
except ImportError:  # pragma: no cover - supports local execution
    from auth import ROLE_MANAGER, ROLE_RECEPTIONIST, require_role
    from config import MCP_ENABLED, MCP_SERVER_URL, MCP_TIMEOUT
    from responses import ok
    from services.mcp_client import (
        MCPDisabledError,
        MCPInvalidResponse,
        MCPTimeout,
        MCPToolFailure,
        MCPUnavailable,
        WardOccupancyMCPClient,
    )

bp = Blueprint("mcp", __name__, url_prefix="/api/mcp")


@bp.get("/ward-occupancy")
def ward_occupancy():
    require_role(ROLE_MANAGER, ROLE_RECEPTIONIST)
    client = WardOccupancyMCPClient(
        enabled=MCP_ENABLED,
        server_url=MCP_SERVER_URL,
        timeout=MCP_TIMEOUT,
    )
    try:
        return ok(client.get_ward_occupancy())
    except MCPDisabledError:
        return ok({"error": "mcp_disabled"}, 503)
    except MCPTimeout:
        return ok({"error": "mcp_timeout"}, 504)
    except MCPUnavailable:
        return ok({"error": "mcp_unavailable"}, 503)
    except MCPToolFailure:
        return ok({"error": "mcp_tool_failure"}, 502)
    except MCPInvalidResponse:
        return ok({"error": "mcp_invalid_response"}, 502)