"""Manager-only read access to shared HOMS MCP tools."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from authorization import require_manager
from errors import (
    MCPBadGatewayError,
    MCPTimeoutError,
    MCPUnavailableError,
    NotFoundError,
    ValidationError,
)
from services.mcp_client import (
    MAX_WARD_LENGTH,
    MCPDisabledError,
    MCPInvalidResponse,
    MCPTimeout,
    MCPToolFailure,
    MCPUnavailable,
    WardOccupancyMCPClient,
)

mcp_blueprint = Blueprint("mcp", __name__, url_prefix="/api/mcp")


@mcp_blueprint.get("/ward-occupancy")
def ward_occupancy():
    """Return privacy-safe Room & Bed occupancy through the shared MCP."""
    require_manager()

    unsupported = sorted(set(request.args) - {"ward"})
    if unsupported:
        raise ValidationError("Unsupported query parameter.",
                              {"parameters": unsupported})
    wards = request.args.getlist("ward")
    if len(wards) > 1:
        raise ValidationError("'ward' must be supplied at most once.",
                              {"field": "ward"})
    ward = wards[0] if wards else None
    if ward is not None and not ward.strip():
        raise ValidationError("'ward' must not be blank.", {"field": "ward"})
    if ward is not None and len(ward) > MAX_WARD_LENGTH:
        raise ValidationError(
            f"'ward' must be at most {MAX_WARD_LENGTH} characters.",
            {"field": "ward", "max_length": MAX_WARD_LENGTH},
        )

    client = WardOccupancyMCPClient(
        enabled=current_app.config["MCP_ENABLED"],
        server_url=current_app.config["MCP_SERVER_URL"],
        timeout=current_app.config["MCP_TIMEOUT"],
    )
    try:
        return jsonify(client.get_ward_occupancy(ward))
    except MCPDisabledError as error:
        raise MCPUnavailableError("Ward occupancy via MCP is not enabled.") from error
    except MCPUnavailable as error:
        raise MCPUnavailableError(
            "Ward occupancy is temporarily unavailable."
        ) from error
    except MCPTimeout as error:
        raise MCPTimeoutError("Ward occupancy request timed out.") from error
    except MCPToolFailure as error:
        if error.code == "WARD_NOT_FOUND":
            raise NotFoundError("Ward not found.") from error
        if error.code == "upstream_timeout":
            raise MCPTimeoutError("Ward occupancy request timed out.") from error
        if error.code == "upstream_unavailable":
            raise MCPUnavailableError(
                "Room & Bed occupancy is temporarily unavailable."
            ) from error
        raise MCPBadGatewayError("Ward occupancy returned an invalid response.") from error
    except MCPInvalidResponse as error:
        raise MCPBadGatewayError("Ward occupancy returned an invalid response.") from error
