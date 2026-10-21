"""Frontend -> backend -> shared Multi-Agent Server for bed shortage cases.

Flow (Release 2): the coordinator starts a workflow for one shortage case;
the shared server runs Planner -> Worker -> Reviewer on the case evidence and
stops at Human Review; the coordinator approves, corrects, partially accepts
or rejects the proposal. Nothing is reserved by the agents. An approved bed
is reserved only when the coordinator records it through the existing
PUT /api/shortage-cases/<id>/decide.

Every route answers when the server is disabled (the cloud default) or
unreachable, so the rest of the feature is never affected.
"""

from flask import Blueprint, request

from auth import require_permission
from responses import ok, ApiError
from services import database_client as dbc
from services import multi_agent_client as mac
from services import multi_agent_evidence as evidence

bp = Blueprint("multi_agent", __name__)

FAILURE_STATUS = {"disabled": 503, "unavailable": 502, "timeout": 504, "not_found": 404,
                  "conflict": 409, "invalid_request": 400, "invalid_response": 502}
CLOSED_CASE_STATUSES = ("Resolved", "Cancelled")


def _unwrap(result):
    if not result.ok:
        raise ApiError(result.error or "Multi-Agent request failed",
                       status=FAILURE_STATUS.get(result.outcome, 502))
    return result.data


def _open_case(case_id):
    case = dbc.get_record("shortage-cases", case_id)
    if case["status"] in CLOSED_CASE_STATUSES:
        raise ApiError("Case {} is already {}".format(case_id, case["status"].lower()), status=409)
    return case


def _with_recommendation(workflow, submitted):
    """Add the case id and the bed the agents recommend, read from the stored evidence."""
    options = {o.get("id"): o for o in (submitted or {}).get("options", []) if isinstance(o, dict)}
    review = (workflow.get("reviewer") or {}).get("output") or {}
    work = (workflow.get("worker") or {}).get("output") or {}
    chosen = review.get("recommended_option_id") or \
        ((work.get("proposed_solution") or {}).get("selected_option_id"))
    option = options.get(chosen)
    workflow["case_id"] = (submitted or {}).get("case_id")
    workflow["recommendation"] = None if not option else {
        "option_id": chosen,
        "label": option.get("label"),
        "bed_id": evidence.bed_id_from_option(chosen),
        "kind": (option.get("attributes") or {}).get("kind"),
    }
    return workflow


def _owned_workflow(workflow_id):
    """Fetch a workflow and refuse one that was not started by this feature."""
    workflow = _unwrap(mac.get(workflow_id, include_evidence=True))
    submitted = workflow.pop("submitted_evidence", None)
    if not isinstance(submitted, dict) or submitted.get("evidence_type") != evidence.EVIDENCE_TYPE:
        raise ApiError("Workflow not found for the room and bed feature", status=404)
    return _with_recommendation(workflow, submitted)


@bp.get("/multi-agent/status")
def status():
    """Whether Multi-Agent mode is on and the shared server answers."""
    return ok(mac.status())


@bp.get("/shortage-cases/<int:case_id>/multi-agent/evidence")
def case_evidence(case_id):
    """The exact evidence the agents receive; also usable from the terminal:
    save it to a file and run ai-services/multi-agent-server/main.py --evidence FILE."""
    case = dbc.get_record("shortage-cases", case_id)
    return ok({"task": evidence.task_for(case), "evidence": evidence.build(case)})


@bp.post("/shortage-cases/<int:case_id>/multi-agent")
def start_workflow(case_id):
    require_permission("bed.allocate")
    case = _open_case(case_id)
    document = evidence.build(case)
    workflow = _unwrap(mac.start(evidence.task_for(case), document))
    return ok(_with_recommendation(workflow, document), status=202)


@bp.get("/shortage-cases/<int:case_id>/multi-agent")
def case_history(case_id):
    dbc.get_record("shortage-cases", case_id)
    return ok(_unwrap(mac.history(evidence.EVIDENCE_TYPE, case_id)))


@bp.get("/multi-agent/workflows/<workflow_id>")
def get_workflow(workflow_id):
    return ok(_owned_workflow(workflow_id))


@bp.post("/multi-agent/workflows/<workflow_id>/review")
def review_workflow(workflow_id):
    """Record the coordinator's Human Review decision on the agents' proposal."""
    user = require_permission("bed.allocate")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("Request body must be a JSON object")
    if set(payload) - {"decision", "comment"}:
        raise ApiError("Only 'decision' and 'comment' are accepted")
    decision = payload.get("decision")
    if decision not in mac.DECISIONS:
        raise ApiError("decision must be one of: {}".format(", ".join(mac.DECISIONS)))
    comment = payload.get("comment") or ""
    if not isinstance(comment, str):
        raise ApiError("comment must be a string")
    comment = " ".join(comment.split())
    if decision == "correct" and not comment:
        raise ApiError("A correction must say what to change")
    if len(comment) > mac.MAX_COMMENT_LENGTH:
        raise ApiError("comment must be at most {} characters".format(mac.MAX_COMMENT_LENGTH))

    _owned_workflow(workflow_id)
    reviewed = "Reviewer: {}. {}".format(user["username"], comment).strip()
    _unwrap(mac.review(workflow_id, decision, reviewed))
    return ok(_owned_workflow(workflow_id))
