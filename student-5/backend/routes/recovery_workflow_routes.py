"""Manager-only proxy for the shared Staffing Recovery workflow lifecycle."""

from __future__ import annotations

from typing import Any, Callable, Dict

from flask import Blueprint, current_app, jsonify, request

from authorization import require_manager
from errors import (
    ConflictError,
    MultiAgentBadGatewayError,
    MultiAgentTimeoutError,
    MultiAgentUnavailableError,
    MultiAgentUpstreamError,
    NotFoundError,
    ValidationError,
)
from services.multi_agent_client import (
    MAX_COMMENT_LENGTH,
    REVIEW_DECISION_VALUES,
    MultiAgentClient,
    MultiAgentConflict,
    MultiAgentHTTPError,
    MultiAgentInputError,
    MultiAgentInvalidResponse,
    MultiAgentNotFound,
    MultiAgentTimeout,
    MultiAgentUnavailable,
)
from validation import require_fields, require_json, validate_choice


recovery_workflow_blueprint = Blueprint(
    "recovery_workflow", __name__, url_prefix="/api/recovery-workflows"
)
REVIEW_FIELDS = {"decision", "comment"}
RECOVERY_EVIDENCE_TYPE = "student5_staffing_recovery"
HISTORY_DISPLAY_LIMIT = 10


def _client() -> MultiAgentClient:
    if not current_app.config["MULTI_AGENT_ENABLED"]:
        raise MultiAgentUnavailableError(
            "The shared Multi-Agent workflow service is disabled."
        )
    return MultiAgentClient(
        base_url=current_app.config["MULTI_AGENT_BASE_URL"],
        timeout=current_app.config["MULTI_AGENT_TIMEOUT"],
    )


def _shared_call(operation: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Map adapter failures onto the existing safe Student 5 API errors."""

    try:
        return operation()
    except MultiAgentInputError as error:
        raise ValidationError(
            "The workflow ID or Human Review data is invalid."
        ) from error
    except MultiAgentNotFound as error:
        raise NotFoundError("Workflow not found.") from error
    except MultiAgentConflict as error:
        raise ConflictError("Workflow is not awaiting Human Review.") from error
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


def _review_request() -> tuple[str, str]:
    payload = require_json(request.get_json(silent=True))
    unexpected = sorted(set(payload) - REVIEW_FIELDS)
    if unexpected:
        raise ValidationError(
            "Unexpected field(s): " + ", ".join(unexpected),
            {"unexpected_fields": unexpected},
        )
    require_fields(payload, ("decision",))
    decision = validate_choice(
        payload["decision"], REVIEW_DECISION_VALUES, "decision"
    )
    comment = payload.get("comment", "")
    if not isinstance(comment, str):
        raise ValidationError("'comment' must be a string.", {"field": "comment"})
    comment = comment.strip()
    if len(comment) > MAX_COMMENT_LENGTH:
        raise ValidationError(
            f"'comment' must be at most {MAX_COMMENT_LENGTH} characters.",
            {"field": "comment", "max_length": MAX_COMMENT_LENGTH},
        )
    if decision == "correct" and not comment:
        raise ValidationError(
            "'comment' is required when decision is correct.",
            {"field": "comment"},
        )
    return decision, comment


def _history_shift_id() -> int:
    unexpected = sorted(set(request.args) - {"shift_id"})
    if unexpected:
        raise ValidationError(
            "Unexpected query field(s): " + ", ".join(unexpected),
            {"unexpected_fields": unexpected},
        )
    raw_shift_id = request.args.get("shift_id")
    try:
        shift_id = int(raw_shift_id)
    except (TypeError, ValueError) as error:
        raise ValidationError(
            "'shift_id' must be a positive integer.", {"field": "shift_id"}
        ) from error
    if shift_id <= 0:
        raise ValidationError(
            "'shift_id' must be a positive integer.", {"field": "shift_id"}
        )
    return shift_id


@recovery_workflow_blueprint.get("")
@recovery_workflow_blueprint.get("/")
def list_recovery_workflows():
    """List recent persisted recovery runs for one shift, without audit copying."""

    require_manager()
    shift_id = _history_shift_id()
    client = _client()
    history = _shared_call(
        lambda: client.list_workflows(
            RECOVERY_EVIDENCE_TYPE,
            evidence_path="current_state.shift.shift_id",
            evidence_value=str(shift_id),
            limit=HISTORY_DISPLAY_LIMIT,
        )
    )
    matches = []
    for item in history["workflows"]:
        human_review = item.get("human_review")
        matches.append(
            {
                "workflow_id": item["workflow_id"],
                "shift_id": shift_id,
                "started_at": item["started_at"],
                "status": item["status"],
                "human_decision": (
                    human_review.get("decision")
                    if isinstance(human_review, dict) else None
                ),
            }
        )
        if len(matches) == HISTORY_DISPLAY_LIMIT:
            break
    return jsonify({"count": len(matches), "workflows": matches})


@recovery_workflow_blueprint.get("/<workflow_id>")
def get_recovery_workflow(workflow_id: str):
    """Return the validated shared workflow state without changing a roster."""

    require_manager()
    client = _client()
    workflow = _shared_call(lambda: client.get_workflow(workflow_id))
    return jsonify(workflow)


@recovery_workflow_blueprint.post("/<workflow_id>/review")
def submit_recovery_workflow_review(workflow_id: str):
    """Record advisory Human Review; never execute a staff assignment."""

    require_manager()
    decision, comment = _review_request()
    client = _client()
    workflow = _shared_call(
        lambda: client.submit_review(workflow_id, decision, comment)
    )
    return jsonify(workflow)
