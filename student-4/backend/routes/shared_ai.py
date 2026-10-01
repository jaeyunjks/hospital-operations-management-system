"""Frontend -> backend -> shared MCP / RAG access for Room & Bed.

The frontend never calls a shared server directly. These routes are the
feature boundary: they validate input, forward it through the bounded
clients, and return the team response envelope. Both servers are optional,
so every route answers even when they are disabled or unreachable.
"""

import json

from flask import Blueprint, request

from responses import ok, ApiError
from services import mcp_client, rag_client
from validation import require_fields

bp = Blueprint("shared_ai", __name__)

MAX_MCP_ARGUMENTS_BYTES = 2_000
MCP_FAILURE_STATUS = {"disabled": 503, "unavailable": 502, "timeout": 504, "tool_error": 502}
RAG_FAILURE_STATUS = {"disabled": 503, "unavailable": 502, "rag_error": 502, "timeout": 504}


@bp.get("/mcp/status")
def mcp_status():
    """Report whether MCP mode is on and which shared tools this feature may use."""
    return ok(mcp_client.status())


@bp.post("/mcp/call")
def mcp_call():
    """Forward one allowlisted, read-only tool call to the shared MCP server."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Request body must be a JSON object")
    require_fields(payload, "tool")

    tool = payload["tool"]
    arguments = payload.get("arguments", {})
    if tool not in mcp_client.FEATURE_TOOLS:
        raise ApiError("{} is not available to the room and bed feature".format(tool), status=403)
    if not isinstance(arguments, dict):
        raise ApiError("arguments must be a JSON object")
    if len(json.dumps(arguments)) > MAX_MCP_ARGUMENTS_BYTES:
        raise ApiError("arguments are too large")

    result = mcp_client.call_tool(tool, arguments)
    if not result.ok:
        raise ApiError(result.error or "MCP tool call failed",
                       status=MCP_FAILURE_STATUS.get(result.outcome, 502))
    return ok(result.to_dict())


@bp.get("/rag/status")
def rag_status():
    """Report whether RAG mode is on and which room and bed documents are indexed."""
    return ok(rag_client.status())


@bp.post("/rag/ask")
def rag_ask():
    """Ask the shared RAG server one question about room and bed operations.

    An insufficient-context answer is a successful outcome, not an error:
    the server declining to guess is exactly the behaviour being validated.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Request body must be a JSON object")
    if set(payload) - {"question"}:
        raise ApiError("Only the 'question' field is accepted")
    require_fields(payload, "question")

    question = payload["question"]
    if not isinstance(question, str):
        raise ApiError("question must be a string")
    question = " ".join(question.split())
    if not question:
        raise ApiError("question must not be blank")
    if len(question) > rag_client.MAX_QUESTION_LENGTH:
        raise ApiError("question must be at most {} characters".format(
            rag_client.MAX_QUESTION_LENGTH))

    result = rag_client.ask(question)
    if not result.ok:
        raise ApiError(result.error or "RAG request failed",
                       status=RAG_FAILURE_STATUS.get(result.outcome, 502))
    return ok(result.to_dict())
