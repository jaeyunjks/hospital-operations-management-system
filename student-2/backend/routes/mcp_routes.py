"""
mcp_routes.py - Flask Blueprint that lets the frontend use ONE shared MCP tool.

Mount at /api/mcp in app.py:

    POST /call     run homs_open_care_tasks for one admission
    GET  /status   report whether that tool is usable right now

DESIGN RULES:

  * Order of checks on POST /call, always the same:
        1. role     - require_role runs first, so a blocked role gets the same
                      403 whatever the configuration is.
        2. switch   - MCP_ENABLED off -> "disabled", nothing is contacted.
        3. body     - JSON object, allowed tool, valid admission_id.
        4. call     - one call through services.mcp_client.
    A blocked role therefore never learns whether MCP is on, which tools exist
    or what the server address is.

  * Exactly one tool name is allowed. Anything else is refused before the
    client is called, so this endpoint can never reach another feature's tools.

  * Replies never carry tracebacks, server addresses or upstream text. Tool
    errors pass their short code through, with fixed messages of our own.

Settings are read on every request (no module-level state).
"""

from flask import Blueprint, jsonify, request

import settings
from auth import AuthError, require_role
from services import mcp_client

mcp_bp = Blueprint("mcp_routes", __name__)

# The only tool this endpoint will ever call.
ALLOWED_TOOL = "homs_open_care_tasks"

# Tool error code -> HTTP status. Codes not listed here become 502.
_TOOL_ERROR_HTTP = {
    "invalid_input": 400,
    "not_found": 404,
    "upstream_unavailable": 503,
}

# Fixed messages for the codes above; anything else gets the generic one.
_TOOL_ERROR_MESSAGE = {
    "invalid_input": "The tool rejected the request.",
    "not_found": "No clinical records were found for this admission.",
    "upstream_unavailable": "The clinical data source is temporarily unavailable.",
}


# ============================================================
# Small helpers
# ============================================================
def _reply(status, http_status, code=None, message=None):
    """Uniform JSON body: a status word, plus an error object when there is one."""
    body = {"status": status}
    if code is not None:
        body["error"] = {"code": code, "message": message}
    return jsonify(body), http_status


def _bad_request(message):
    return _reply("error", 400, "invalid_request", message)


def _validate_call_body():
    """
    Check the POST body. Returns (admission_id, None) when valid, or
    (None, error_response) when not. Nothing is echoed back from the body.
    """
    body = request.get_json(silent=True)  # None if missing or not JSON
    if not isinstance(body, dict):
        return None, _bad_request("request body must be a JSON object")

    # Only one tool is allowed; compare by equality on a real string.
    tool = body.get("tool")
    if not isinstance(tool, str) or tool != ALLOWED_TOOL:
        return None, _bad_request("unsupported tool")

    arguments = body.get("arguments")
    if not isinstance(arguments, dict):
        return None, _bad_request("'arguments' must be a JSON object")
    if set(arguments) - {"admission_id"}:
        return None, _bad_request("unsupported argument")

    # `type(...) is int` rejects true/false, 1.0 and "1" as well.
    admission_id = arguments.get("admission_id")
    if type(admission_id) is not int or admission_id < 1:
        return None, _bad_request("'admission_id' must be a positive whole number")

    return admission_id, None


# ============================================================
# POST /api/mcp/call
# Body: {"tool": "homs_open_care_tasks", "arguments": {"admission_id": <int>}}
# ============================================================
@mcp_bp.post("/call")
@require_role("doctor", "nurse", "specialist")   # 1. role (raises AuthError)
def call_tool():
    # 2. switch - checked before the body so an "off" service reacts the same
    #    to every request.
    if not settings.MCP_ENABLED():
        return _reply("disabled", 503, "mcp_disabled", "MCP is not enabled.")

    # 3. body
    admission_id, error = _validate_call_body()
    if error:
        return error

    # 4. call, then map the outcome.
    try:
        data = mcp_client.call_tool(ALLOWED_TOOL, {"admission_id": admission_id})
    except mcp_client.McpDisabled:
        # Switched off between the check above and the call.
        return _reply("disabled", 503, "mcp_disabled", "MCP is not enabled.")
    except mcp_client.McpUnavailable:
        return _reply("unavailable", 503, "mcp_unavailable",
                      "The MCP server is temporarily unavailable.")
    except mcp_client.McpToolError as exc:
        # Pass the tool's own code through (already a safe short identifier).
        http_status = _TOOL_ERROR_HTTP.get(exc.code, 502)
        message = _TOOL_ERROR_MESSAGE.get(exc.code, "The tool reported an error.")
        return _reply("error", http_status, exc.code, message)
    except mcp_client.McpError:
        # McpBadReply, or any other MCP failure we did not classify above.
        return _reply("error", 502, "mcp_invalid_response",
                      "The MCP server returned an invalid response.")

    return jsonify({"status": "ok", "tool": ALLOWED_TOOL, "result": data}), 200


# ============================================================
# GET /api/mcp/status
# "disabled" | "ok" | "unavailable" - always HTTP 200 for a clinical role,
# because the state itself is the answer, not a failure of this route.
# ============================================================
@mcp_bp.get("/status")
@require_role("doctor", "nurse", "specialist")
def mcp_status():
    info = mcp_client.get_status()  # never raises

    if not info.get("enabled"):
        state = "disabled"
    elif info.get("reachable") and ALLOWED_TOOL in info.get("tools", []):
        # "ok" means OUR tool is listed; other tools are never reported.
        state = "ok"
    else:
        state = "unavailable"

    return jsonify({"status": state, "tool": ALLOWED_TOOL}), 200


# ------------------------------------------------------------
# Blueprint-local error handler - same style as ai_summary.py.
# ------------------------------------------------------------
@mcp_bp.errorhandler(AuthError)
def _on_auth_error(err):
    # err.message is fixed text about roles only; it says nothing about config.
    return _reply("error", err.status, "forbidden" if err.status == 403 else "unauthorised",
                  err.message)
