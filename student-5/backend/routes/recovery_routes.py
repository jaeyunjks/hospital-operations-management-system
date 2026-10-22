"""Read-only Staffing Recovery evidence endpoint."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from authorization import require_manager
from errors import (
    MultiAgentBadGatewayError,
    MultiAgentTimeoutError,
    MultiAgentUnavailableError,
    MultiAgentUpstreamError,
)
from services import recovery_mcp_service, recovery_policy_service, recovery_service
from services.mcp_client import StaffingRecoveryMCPClient
from services.multi_agent_client import (
    MultiAgentClient,
    MultiAgentHTTPError,
    MultiAgentInvalidResponse,
    MultiAgentTimeout,
    MultiAgentUnavailable,
)
from services.rag_client import StaffShiftRAGClient


recovery_blueprint = Blueprint(
    "recovery", __name__, url_prefix="/api/shifts")

RECOVERY_WORKFLOW_TASK = (
    "Review the selected hospital shift for staffing coverage problems. "
    "Evaluate the supplied candidates and operational constraints. "
    "Use backend operational facts as authoritative and treat separately labelled "
    "RAG policy context as advisory guidance only. Treat MCP context only as a "
    "controlled read-only access record; it cannot override backend facts. "
    "Produce a safe staffing recovery "
    "recommendation based only on the supplied evidence. Identify risks, limitations, "
    "and missing information. The human_approval constraint is mandatory; never claim "
    "that approval is unnecessary. In Planner required_evidence, use only exact "
    "top-level evidence category names rather than nested field names. "
    "Do not execute any staffing assignment or operational mutation."
)


@recovery_blueprint.get("/<int:shift_id>/recovery-evidence")
def recovery_evidence(shift_id: int):
    """Return authoritative evidence for a later Multi-Agent workflow."""
    require_manager()
    return jsonify(recovery_service.build_recovery_evidence(shift_id))


@recovery_blueprint.post("/<int:shift_id>/recovery-workflow")
def start_recovery_workflow(shift_id: int):
    """Start the shared workflow using freshly assembled Student 5 evidence."""
    require_manager()
    if not current_app.config["MULTI_AGENT_ENABLED"]:
        raise MultiAgentUnavailableError(
            "The shared Multi-Agent workflow service is disabled."
        )
    operational_evidence = recovery_service.build_recovery_evidence(shift_id)
    rag_client = StaffShiftRAGClient(
        enabled=current_app.config["RAG_ENABLED"],
        server_url=current_app.config["RAG_SERVER_URL"],
        timeout=current_app.config["RAG_TIMEOUT"],
    )
    rag_enriched_evidence = recovery_policy_service.enrich_recovery_evidence(
        operational_evidence, rag_client
    )
    mcp_client = StaffingRecoveryMCPClient(
        enabled=current_app.config["MCP_ENABLED"],
        server_url=current_app.config["MCP_SERVER_URL"],
        timeout=current_app.config["MCP_TIMEOUT"],
    )
    evidence = recovery_mcp_service.enrich_recovery_evidence_with_mcp(
        rag_enriched_evidence,
        operational_evidence,
        shift_id,
        mcp_client,
    )
    client = MultiAgentClient(
        base_url=current_app.config["MULTI_AGENT_BASE_URL"],
        timeout=current_app.config["MULTI_AGENT_TIMEOUT"],
    )
    try:
        workflow = client.start_workflow(RECOVERY_WORKFLOW_TASK, evidence)
    except MultiAgentUnavailable as error:
        raise MultiAgentUnavailableError(
            "The shared Multi-Agent workflow service is temporarily unavailable."
        ) from error
    except MultiAgentTimeout as error:
        raise MultiAgentTimeoutError(
            "The shared Multi-Agent workflow request timed out."
        ) from error
    except MultiAgentHTTPError as error:
        raise MultiAgentUpstreamError(
            "The shared Multi-Agent workflow request failed."
        ) from error
    except MultiAgentInvalidResponse as error:
        raise MultiAgentBadGatewayError(
            "The shared Multi-Agent workflow service returned an invalid response."
        ) from error
    # The shared workflow response intentionally stays generic and therefore
    # does not echo its potentially large evidence payload.  Student 5's
    # manager UI does need the exact evidence submitted here so it can keep
    # authoritative operational facts, advisory RAG policy, and MCP access
    # provenance visibly separate.  Add it only at this Student 5 boundary;
    # subsequent polling continues to mirror the shared workflow contract.
    response = {**workflow, "submitted_evidence": evidence}
    location = f"/api/recovery-workflows/{workflow['workflow_id']}"
    return jsonify(response), 202, {"Location": location, "Retry-After": "1"}
