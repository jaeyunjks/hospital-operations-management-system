"""Controlled MCP enrichment for the Staffing Recovery workflow.

The original Student 5 evidence remains authoritative. MCP demonstrates a
bounded registered-tool access path and is never allowed to replace or mutate
that evidence.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

from services.mcp_client import (
    MCPDisabledError,
    MCPInvalidResponse,
    MCPTimeout,
    MCPToolFailure,
    MCPUnavailable,
    STAFFING_RECOVERY_TOOL_NAME,
    StaffingRecoveryMCPClient,
)


MCP_LIMITATIONS = [
    (
        "MCP provides controlled read access to Student 5 operational evidence; "
        "it is not policy guidance."
    ),
    "The original backend evidence remains authoritative if MCP data differs or is unavailable.",
    "The registered tool cannot create, update, delete, approve, or execute a roster change.",
    "Human Review remains mandatory before any later assignment action.",
]


def _unavailable_context(shift_id: int, reason: str, error_code: str | None = None):
    return {
        "context_type": "mcp_operational_access",
        "retrieval_status": "unavailable",
        "tool_name": STAFFING_RECOVERY_TOOL_NAME,
        "tool_input": {"shift_id": shift_id},
        "tool_result": None,
        "source": None,
        "error_code": error_code,
        "reason": reason,
        "consistency_status": "not_checked",
        "limitations": list(MCP_LIMITATIONS),
    }


def _retrieved_context(
    shift_id: int,
    result: Dict[str, Any],
    operational_evidence: Dict[str, Any],
) -> Dict[str, Any]:
    data = result["data"]
    if data["evidence"] != operational_evidence:
        return {
            "context_type": "mcp_operational_access",
            "retrieval_status": "inconsistent",
            "tool_name": STAFFING_RECOVERY_TOOL_NAME,
            "tool_input": {"shift_id": shift_id},
            "tool_result": None,
            "source": data["source"],
            "error_code": None,
            "reason": "mcp_evidence_mismatch",
            "consistency_status": "mismatch",
            "limitations": list(MCP_LIMITATIONS),
        }
    return {
        "context_type": "mcp_operational_access",
        "retrieval_status": "available",
        "tool_name": STAFFING_RECOVERY_TOOL_NAME,
        "tool_input": {"shift_id": shift_id},
        "tool_result": copy.deepcopy(data),
        "source": data["source"],
        "error_code": None,
        "reason": None,
        "consistency_status": "matched_authoritative_backend_evidence",
        "limitations": list(MCP_LIMITATIONS),
    }


def enrich_recovery_evidence_with_mcp(
    enriched_evidence: Dict[str, Any],
    operational_evidence: Dict[str, Any],
    shift_id: int,
    mcp_client: StaffingRecoveryMCPClient,
) -> Dict[str, Any]:
    """Add explicit MCP context without altering operational or RAG evidence."""

    evidence = copy.deepcopy(enriched_evidence)
    try:
        result = mcp_client.get_recovery_context(shift_id)
    except MCPDisabledError:
        context = _unavailable_context(shift_id, "mcp_disabled")
    except MCPTimeout:
        context = _unavailable_context(shift_id, "mcp_timeout")
    except MCPUnavailable:
        context = _unavailable_context(shift_id, "mcp_unavailable")
    except MCPInvalidResponse:
        context = _unavailable_context(shift_id, "mcp_invalid_response")
    except MCPToolFailure as error:
        reason = {
            "upstream_timeout": "mcp_timeout",
            "upstream_unavailable": "mcp_unavailable",
            "upstream_invalid_response": "mcp_invalid_response",
        }.get(error.code, "mcp_tool_error")
        context = _unavailable_context(shift_id, reason, error.code)
    else:
        context = _retrieved_context(shift_id, result, operational_evidence)

    separation = copy.deepcopy(evidence.get("evidence_separation", {}))
    separation.update({
        "mcp_context_field": "mcp_context",
        "mcp_is_controlled_operational_access": True,
        "mcp_cannot_override_operational_rules": True,
    })
    evidence["evidence_separation"] = separation
    evidence["mcp_context"] = context
    return evidence
