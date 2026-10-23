"""
multi_agent_routes.py - Flask Blueprint for the AI discharge readiness review.

Mount at /api/multi-agent in app.py:

    GET  /status                   is the review usable right now?
    POST /start                    start a review for one admission
    GET  /workflows/<id>           progress of one review
    POST /workflows/<id>/review    record the doctor's decision
    GET  /latest?admission_id=N    the newest review of one admission

DESIGN RULES:

  * The AI is advisory only. Nothing here discharges a patient, and nothing
    here calls discharge-approve or any service other than the Multi-Agent
    Server (through services.multi_agent_client) and the database API.

  * Order of checks, always the same:
        1. role     - require_role runs first, so a blocked role gets the same
                      403 whatever the configuration is.
        2. switch   - MULTI_AGENT_ENABLED off -> "disabled", nothing contacted.
        3. request  - body or query is valid.
        4. doctor   - the caller is an assigned doctor on the admission.
        5. call     - the write or lookup itself.
    require_assignment takes ONE staff id from the web address, but here the
    admission id is in the body (or in the workflow's stored evidence) and an
    admission can have several doctors, so step 4 is done inside each route
    with assigned_doctor_ids. For the two workflow routes the admission is
    only known from the workflow, so step 5 starts with a read-only get();
    nothing is written until step 4 has passed.

  * Replies are an allowlist (see _view). Raw agent output, evidence, errors
    and server text never leave this module, and every message is fixed text.
    A database fault gets a fixed message too - never str(exc), which can
    carry addresses.

  * A review is saved as ONE ai_summaries row, already final, and only after
    the server returned ok - so a second click (409) never makes a second row.
    If the save fails, the reply is still a success with "logged": false and
    the server is not called again.

Settings are read on every request (no module-level state).
"""

import logging

from flask import Blueprint, jsonify, request

import settings
from auth import AuthError, get_current_user, require_role
from services import admission_data, discharge_evidence, multi_agent_client
from services import database_client as db

multi_agent_bp = Blueprint("multi_agent_routes", __name__)

logger = logging.getLogger("student2.multi_agent")

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
MAX_COMMENT_CHARS = 1000
MAX_TEXT_CHARS = 500   # one reviewer sentence or risk, in a reply or a saved row
MAX_RISKS = 10
MAX_MODEL_CHARS = 80

SCOPE_DISCHARGE_READINESS = "discharge_readiness"

# Must match _REQUESTED_BY_PREFIX in ai_summary.py: the review route there reads
# the requester back from source_reference with this prefix.
_REQUESTED_BY_PREFIX = "requested_by_staff_id="

# The doctor's decision -> ai_summaries.review_status (CHECK list in schema.sql).
_REVIEW_STATUS = {
    "approve": "accepted",
    "correct": "edited",
    "reject": "rejected",
    "partially_accept": "partially_accepted",
}

AWAITING_HUMAN_REVIEW = "awaiting_human_review"
# Workflow status words the server may report; anything else is shown as "unknown".
_KNOWN_STATUSES = frozenset({
    "in_progress", AWAITING_HUMAN_REVIEW, "failed",
    "approved", "corrected", "rejected", "partially_accepted",
})

RULE_BASED = "rule-based"   # a stage the server answered without a live model

# Short labels for the check ids in discharge_evidence.CHECKS.
_CHECK_LABELS = {
    "no_open_care_tasks": "No open care tasks",
    "no_pending_consultations": "No pending consultations",
    "no_scheduled_surgery": "No scheduled surgery",
}

# multi_agent_client outcome -> (status word, HTTP status, code, message).
_FAILURES = {
    multi_agent_client.DISABLED: (
        "disabled", 503, "multi_agent_disabled", "Multi-Agent review is not enabled."),
    multi_agent_client.UNAVAILABLE: (
        "unavailable", 503, "multi_agent_unavailable",
        "The Multi-Agent Server is temporarily unavailable."),
    multi_agent_client.TIMEOUT: (
        "unavailable", 504, "multi_agent_timeout",
        "The Multi-Agent Server did not answer in time."),
    multi_agent_client.NOT_FOUND: (
        "error", 404, "workflow_not_found", "Discharge review not found."),
    multi_agent_client.INVALID_STATE: (
        "error", 409, "invalid_state", "This review cannot be changed in its current state."),
    multi_agent_client.REJECTED: (
        "error", 502, "multi_agent_rejected",
        "The Multi-Agent Server did not accept the request."),
}
_GENERIC_FAILURE = (
    "error", 502, "multi_agent_invalid_response",
    "The Multi-Agent Server returned an invalid response.")


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


def _switch_off():
    """The 'disabled' reply when the integration is off, else None."""
    if settings.MULTI_AGENT_ENABLED():
        return None
    return _reply("disabled", 503, "multi_agent_disabled", "Multi-Agent review is not enabled.")


def _failure(result):
    """Map a failed multi_agent_client result to a fixed reply."""
    status, http_status, code, message = _FAILURES.get(result.get("outcome"), _GENERIC_FAILURE)
    return _reply(status, http_status, code, message)


def _user_id():
    return (get_current_user() or {}).get("id")


def _is_assigned(admission_id, user_id):
    """True if user_id is a doctor on this admission's clinical records."""
    if admission_id is None or user_id is None:
        return False
    return user_id in admission_data.assigned_doctor_ids(admission_id)


def _not_assigned():
    return _reply("error", 403, "forbidden", "You are not the assigned doctor for this admission.")


def _workflow_not_found():
    return _reply("error", 404, "workflow_not_found", "Discharge review not found.")


def _is_admission_id(value):
    """`type(...) is int` rejects true/false, 1.0 and "1" as well."""
    return type(value) is int and value > 0


def _one_line(value, limit):
    """A string collapsed to one line and cut to `limit`; None if not text or empty."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:limit]
    return text or None


# ============================================================
# The reply view (allowlist only)
# ============================================================
def _stage_view(name, stage):
    """{name, done, source}: source is the model name, or 'rule-based', once done."""
    stage = stage if isinstance(stage, dict) else {}
    done = bool(stage.get("completed_at")) and isinstance(stage.get("output"), dict)
    source = None
    if done:
        model = _one_line(stage.get("model"), MAX_MODEL_CHARS)
        source = RULE_BASED if stage.get("fallback_used") or model is None else model
    return {"name": name, "done": done, "source": source}


def _checks_view(evidence):
    """The pass/fail list from OUR stored evidence; empty if it is not there."""
    options = evidence.get("options") if isinstance(evidence, dict) else None
    first = options[0] if isinstance(options, list) and options else None
    attributes = first.get("attributes") if isinstance(first, dict) else None
    if not isinstance(attributes, dict):
        return []
    return [
        {
            "id": check_id,
            "label": _CHECK_LABELS.get(check_id, check_id),
            "passed": attributes.get(check_id) is True,
        }
        for check_id, _ in discharge_evidence.CHECKS
    ]


def _recommendation_view(workflow, reviewer_done):
    """The Reviewer's recommendation text and risks, as short plain strings."""
    if not reviewer_done:
        return None
    output = workflow["reviewer"]["output"]
    risks = output.get("risks")
    risks = risks if isinstance(risks, list) else []
    return {
        "text": _one_line(output.get("recommendation"), MAX_TEXT_CHARS),
        "risks": [t for t in (_one_line(r, MAX_TEXT_CHARS) for r in risks) if t][:MAX_RISKS],
    }


def _decision_view(human_review):
    """The doctor's decision and comment once made, else None."""
    if not isinstance(human_review, dict):
        return None
    decision = human_review.get("decision")
    if decision not in multi_agent_client.DECISIONS:
        return None
    comment = human_review.get("comments")
    comment = comment.strip()[:MAX_COMMENT_CHARS] if isinstance(comment, str) else ""
    return {"decision": decision, "comment": comment}


def _view(workflow, admission_id, evidence):
    """
    The only shape a workflow ever leaves this module in. `admission_id` is the
    admission the caller was authorised for; `evidence` is our own stored (or
    just-built) evidence, which is where checks and the gap come from.
    """
    stages = [_stage_view(name, workflow.get(name)) for name in multi_agent_client.STAGES]
    done = {stage["name"]: stage["done"] for stage in stages}
    checks = _checks_view(evidence)

    outcome = None
    if checks and done["worker"] and done["reviewer"]:   # no verdict before the Reviewer has validated
        outcome = "ready" if all(c["passed"] for c in checks) else "not_ready"
    gap = None
    if checks:
        gap = {
            "current": sum(1 for c in checks if c["passed"]),
            "target": discharge_evidence.CHECKS_TARGET,
        }

    status = workflow.get("status")
    return {
        "workflow_id": workflow.get("workflow_id"),
        "status": status if status in _KNOWN_STATUSES else "unknown",
        "admission_id": admission_id,
        "stages": stages,
        "outcome": outcome,
        "checks": checks,
        "gap": gap,
        "recommendation": _recommendation_view(workflow, done["reviewer"]),
        "decision": _decision_view(workflow.get("human_review")),
    }


# ============================================================
# Request validation
# ============================================================
def _validate_start_body():
    """Returns (admission_id, None) when valid, or (None, error_response)."""
    body = request.get_json(silent=True)  # None if missing or not JSON
    if not isinstance(body, dict):
        return None, _bad_request("request body must be a JSON object")
    if set(body) - {"admission_id"}:
        return None, _bad_request("unsupported field")
    admission_id = body.get("admission_id")
    if not _is_admission_id(admission_id):
        return None, _bad_request("'admission_id' must be a positive whole number")
    return admission_id, None


def _validate_review_body():
    """Returns (decision, comment, None) when valid, or (None, None, error_response)."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, None, _bad_request("request body must be a JSON object")
    if set(body) - {"decision", "comment"}:
        return None, None, _bad_request("unsupported field")

    decision = body.get("decision")
    if not isinstance(decision, str) or decision not in multi_agent_client.DECISIONS:
        return None, None, _bad_request(
            "'decision' must be one of: {}".format(", ".join(multi_agent_client.DECISIONS))
        )

    comment = body.get("comment")
    if comment is None:
        comment = ""   # a missing comment counts as an empty one
    if not isinstance(comment, str):
        return None, None, _bad_request("'comment' must be text")
    comment = comment.strip()
    if len(comment) > MAX_COMMENT_CHARS:
        return None, None, _bad_request(
            "'comment' must be at most {} characters".format(MAX_COMMENT_CHARS)
        )
    if decision == "correct" and not comment:
        return None, None, _bad_request("'comment' is required when the decision is 'correct'")
    return decision, comment, None


def _validate_latest_query():
    """Returns (admission_id, None) when valid, or (None, error_response)."""
    raw = request.args.get("admission_id", "")
    # Plain ASCII digits only, short enough that int() cannot choke.
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 9) or int(raw) < 1:
        return None, _bad_request("'admission_id' must be a positive whole number")
    return int(raw), None


# ============================================================
# Saving the finished review
# ============================================================
def _summary_text(view, decision, comment):
    """Plain text for ai_summaries.summary_text; 'Workflow: <id>' is the last line."""
    outcome = {"ready": "Ready", "not_ready": "Not ready"}.get(view["outcome"], "Not available")
    gap = view["gap"]
    recommendation = view["recommendation"] or {}
    risks = recommendation.get("risks") or []

    lines = ["Discharge readiness review (AI advisory; the doctor decides)", "Result: " + outcome]
    if gap:
        lines.append("Checks met: {} of {}".format(gap["current"], gap["target"]))
    for check in view["checks"]:
        lines.append("- {}: {}".format(check["label"], "passed" if check["passed"] else "not passed"))
    lines.append("Reviewer note: " + (recommendation.get("text") or "none"))
    lines.append("Reviewer risks: " + ("; ".join(risks) if risks else "none"))
    lines.append("Doctor decision: " + decision)
    lines.append("Doctor comment: " + (_one_line(comment, MAX_COMMENT_CHARS) or "none"))
    lines.append("Workflow: " + view["workflow_id"])
    return "\n".join(lines)


def _save_review(view, decision, comment, user_id):
    """
    Save ONE final ai_summaries row. Returns True if saved, False if not.
    Never raises on a database fault, and never talks to the server.
    """
    try:
        patient_id = admission_data.patient_id_for_admission(view["admission_id"])
        if patient_id is None:   # patient_id is NOT NULL in ai_summaries
            raise db.DatabaseClientError("no patient for this admission")
        stages = {stage["name"]: stage for stage in view["stages"]}
        payload = {
            "admission_id": view["admission_id"],
            "patient_id": patient_id,
            "summary_text": _summary_text(view, decision, comment),
            "model_used": stages["reviewer"]["source"] or RULE_BASED,
            "summary_scope": SCOPE_DISCHARGE_READINESS,
            "review_status": _REVIEW_STATUS[decision],      # already final
            "reviewed_by_staff_id": user_id,
            "source_reference": "{}{}".format(_REQUESTED_BY_PREFIX, user_id),
            "source_documents": [],   # real list; the DB API encodes it
        }
        db.create_ai_summary(payload)
    except db.DatabaseClientError:
        # Fixed text only: the workflow id is a checked UUID, nothing else is logged.
        logger.warning("[MULTI-AGENT] review_save failed workflow=%s", view["workflow_id"])
        return False
    return True


# ============================================================
# GET /api/multi-agent/status
# "disabled" | "ok" | "unavailable" - always HTTP 200 for a clinical role,
# because the state itself is the answer, not a failure of this route.
# ============================================================
@multi_agent_bp.get("/status")
@require_role("doctor", "nurse", "specialist")
def multi_agent_status():
    info = multi_agent_client.get_status()  # never raises

    outcome = info.get("outcome")
    if outcome == multi_agent_client.DISABLED:
        state = "disabled"
    elif outcome == multi_agent_client.OK:
        state = "ok"
    else:
        state = "unavailable"
    return jsonify({"status": state}), 200


# ============================================================
# POST /api/multi-agent/start
# Body: {"admission_id": <int>}
# ============================================================
@multi_agent_bp.post("/start")
@require_role("doctor")                                    # 1. role
def start_review():
    off = _switch_off()                                    # 2. switch
    if off:
        return off

    admission_id, error = _validate_start_body()           # 3. body
    if error:
        return error

    if not _is_assigned(admission_id, _user_id()):         # 4. assigned doctor
        return _not_assigned()

    # 5. build the evidence, then start. The agents run in the background.
    try:
        task, evidence = discharge_evidence.build_discharge_evidence(admission_id)
    except discharge_evidence.NoClinicalRecordsError:
        # Only reachable if the records vanished since the check above.
        return _reply("error", 404, "admission_not_found",
                      "No clinical records were found for this admission.")

    result = multi_agent_client.start(admission_id, task, evidence)
    if result.get("outcome") == multi_agent_client.NOT_FOUND:
        # The server answered with a workflow that is not ours. We just created
        # it, so that is a bad server reply, not a "not found".
        return _reply(*_GENERIC_FAILURE)
    if not result.get("ok"):
        return _failure(result)

    view = _view(result["workflow"], admission_id, evidence)
    return jsonify({"status": "ok", "workflow": view}), 202


# ============================================================
# GET /api/multi-agent/workflows/<id>
# ============================================================
@multi_agent_bp.get("/workflows/<workflow_id>")
@require_role("doctor")                                    # 1. role
def get_workflow(workflow_id):
    off = _switch_off()                                    # 2. switch
    if off:
        return off

    # 3. nothing in a body; the id is checked as a UUID inside the client.
    # 5a. read-only: the admission is only known from the stored evidence.
    result = multi_agent_client.get(workflow_id)
    if not result.get("ok"):
        return _failure(result)
    workflow = result["workflow"]

    # 4. assigned doctor. Not yours reads as not found, so ids confirm nothing.
    admission_id = workflow.get("admission_id")
    if not _is_assigned(admission_id, _user_id()):
        return _workflow_not_found()

    view = _view(workflow, admission_id, workflow.get("submitted_evidence"))
    return jsonify({"status": "ok", "workflow": view}), 200


# ============================================================
# POST /api/multi-agent/workflows/<id>/review
# Body: {"decision": "approve|correct|reject|partially_accept", "comment": "<text>"}
# ============================================================
@multi_agent_bp.post("/workflows/<workflow_id>/review")
@require_role("doctor")                                    # 1. role
def review_workflow(workflow_id):
    off = _switch_off()                                    # 2. switch
    if off:
        return off

    decision, comment, error = _validate_review_body()     # 3. body
    if error:
        return error

    # 5a. read-only: the admission is only known from the stored evidence.
    #     (multi_agent_client.review reads the workflow again before it writes.)
    current = multi_agent_client.get(workflow_id)
    if not current.get("ok"):
        return _failure(current)
    workflow = current["workflow"]
    admission_id = workflow.get("admission_id")
    evidence = workflow.get("submitted_evidence")

    user_id = _user_id()
    if not _is_assigned(admission_id, user_id):            # 4. assigned doctor
        return _workflow_not_found()

    # Only an awaiting review can be decided, and only once.
    if workflow.get("status") != AWAITING_HUMAN_REVIEW or workflow.get("human_review") is not None:
        return _failure({"outcome": multi_agent_client.INVALID_STATE})

    # 5b. record the decision. A 409 from the server stops here: no second row.
    reviewed = multi_agent_client.review(workflow_id, decision, comment)
    if not reviewed.get("ok"):
        return _failure(reviewed)

    view = _view(reviewed["workflow"], admission_id, evidence)
    if view["decision"] is None:   # the server accepted it but did not echo it back
        view["decision"] = {"decision": decision, "comment": comment}

    logged = _save_review(view, decision, comment, user_id)
    return jsonify({"status": "ok", "workflow": view, "logged": logged}), 200


# ============================================================
# GET /api/multi-agent/latest?admission_id=N
# ============================================================
@multi_agent_bp.get("/latest")
@require_role("doctor")                                    # 1. role
def latest_review():
    off = _switch_off()                                    # 2. switch
    if off:
        return off

    admission_id, error = _validate_latest_query()         # 3. query
    if error:
        return error

    if not _is_assigned(admission_id, _user_id()):         # 4. assigned doctor
        return _not_assigned()

    result = multi_agent_client.find_latest(admission_id)  # 5. lookup
    if result.get("outcome") == multi_agent_client.NONE:
        return jsonify({"status": "none"}), 200
    if not result.get("ok"):
        return _failure(result)

    workflow = result["workflow"]
    view = _view(workflow, admission_id, workflow.get("submitted_evidence"))
    return jsonify({"status": "ok", "workflow": view}), 200


# ------------------------------------------------------------
# Blueprint-local error handlers - same style as mcp_routes.py.
# ------------------------------------------------------------
@multi_agent_bp.errorhandler(AuthError)
def _on_auth_error(err):
    # err.message is fixed text about roles only; it says nothing about config.
    return _reply("error", err.status, "forbidden" if err.status == 403 else "unauthorised",
                  err.message)


@multi_agent_bp.errorhandler(db.DatabaseClientError)
def _on_db_error(_err):
    # admission_data touches the database API outside any per-route try/except.
    # The message is fixed on purpose: the exception text can carry addresses.
    return _reply("error", 502, "database_unavailable",
                  "The clinical database is temporarily unavailable.")
