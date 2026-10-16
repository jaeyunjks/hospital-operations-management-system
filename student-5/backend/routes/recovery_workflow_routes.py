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


def _client() -> MultiAgentClient:
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
