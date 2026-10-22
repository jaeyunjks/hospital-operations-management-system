"""Human-reviewed, read-only admission scheduling proposals."""

from flask import Blueprint, request

try:
    from backend.auth import ROLE_MANAGER, ROLE_RECEPTIONIST, require_role
    from backend.config import (
        MULTI_AGENT_ENABLED,
        MULTI_AGENT_SERVER_URL,
        MULTI_AGENT_TIMEOUT,
    )
    from backend.responses import ApiError, ok
    from backend.services import database_client
    from backend.services.admission_scheduling import (
        SchedulingInputError,
        build_review_evidence,
        follow_up_request,
    )
    from backend.services.multi_agent_client import (
        MultiAgentClient,
        MultiAgentError,
    )
except ImportError:  # pragma: no cover - supports local execution
    from auth import ROLE_MANAGER, ROLE_RECEPTIONIST, require_role
    from config import MULTI_AGENT_ENABLED, MULTI_AGENT_SERVER_URL, MULTI_AGENT_TIMEOUT
    from responses import ApiError, ok
    from services import database_client
    from services.admission_scheduling import (
        SchedulingInputError,
        build_review_evidence,
        follow_up_request,
    )
    from services.multi_agent_client import MultiAgentClient, MultiAgentError


bp = Blueprint("multi_agent", __name__, url_prefix="/api/multi-agent")
ALLOWED_ROLES = (ROLE_MANAGER, ROLE_RECEPTIONIST)
DECISIONS = {"approve", "correct", "reject", "partially_accept"}


def _client() -> MultiAgentClient:
    return MultiAgentClient(
        enabled=MULTI_AGENT_ENABLED,
        server_url=MULTI_AGENT_SERVER_URL,
        timeout=MULTI_AGENT_TIMEOUT,
    )


def _raise_client_error(error: MultiAgentError):
    raise ApiError(error.code, status=error.status_code) from error


def _unwrap_admissions(payload):
    while isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if not isinstance(payload, list):
        raise ApiError("Admission schedule data could not be verified.", status=502)
    return payload


def _next_batch(workflow):
    """Build evidence for three times not yet reviewed, or None if none remain."""
    try:
        request_data, reviewed = follow_up_request(workflow.get("submitted_evidence"))
        admission_rows = _unwrap_admissions(database_client.list_admissions())
        return build_review_evidence(request_data, admission_rows, reviewed)
    except ApiError as error:
        raise ApiError("Admission schedule data is unavailable.", status=503) from error
    except SchedulingInputError:
        return None


@bp.post("/admission-scheduling")
def start_admission_scheduling_review():
    require_role(*ALLOWED_ROLES)
    if not MULTI_AGENT_ENABLED:
        raise ApiError("multi_agent_disabled", status=503)
    payload = request.get_json(silent=True)
    try:
        admission_rows = database_client.list_admissions()
    except ApiError as error:
        raise ApiError("Admission schedule data is unavailable.", status=503) from error
    try:
        task, evidence = build_review_evidence(
            payload, _unwrap_admissions(admission_rows)
        )
    except SchedulingInputError as error:
        raise ApiError(str(error), status=400) from error
    try:
        return ok(_client().create_workflow(task, evidence), status=202)
    except MultiAgentError as error:
        _raise_client_error(error)


@bp.get("/admission-scheduling/<workflow_id>")
def get_admission_scheduling_review(workflow_id):
    require_role(*ALLOWED_ROLES)
    try:
        return ok(_client().get_workflow(workflow_id))
    except MultiAgentError as error:
        _raise_client_error(error)


@bp.post("/admission-scheduling/<workflow_id>/review")
def submit_admission_scheduling_review(workflow_id):
    require_role(*ALLOWED_ROLES)
    payload = request.get_json(silent=True)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"decision", "comment"}
        or payload.get("decision") not in DECISIONS
        or not isinstance(payload.get("comment"), str)
        or len(payload["comment"]) > 10_000
        or (payload["decision"] == "correct" and not payload["comment"].strip())
    ):
        raise ApiError("Provide a valid human review decision and comment.", status=400)
    try:
        client = _client()
        follow_up = None
        if payload["decision"] == "partially_accept":
            follow_up = _next_batch(client.get_workflow(workflow_id))
        result = client.submit_review(
            workflow_id, payload["decision"], payload["comment"]
        )
        next_workflow_id = None
        if follow_up is not None:
            created = client.create_workflow(*follow_up)
            next_workflow_id = created.get("workflow_id")
        return ok({**result, "next_workflow_id": next_workflow_id})
    except MultiAgentError as error:
        _raise_client_error(error)
