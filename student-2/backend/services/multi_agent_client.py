"""
multi_agent_client.py - the ONLY module that talks to the shared Multi-Agent
Server (Planner -> Worker -> Reviewer -> Human Review, port 8200).

What it does:
  * start(admission_id, task, evidence)  - begin a discharge review workflow.
  * get(workflow_id)                     - read one workflow, with its evidence.
  * review(workflow_id, decision, comment) - record the doctor's decision.
  * find_latest(admission_id)            - the newest review of one admission.
  * get_status()                         - is the server on and reachable?

SAFETY RULES (the same ideas as Student 3's client):
  * Only workflows this feature started are ever read or reviewed. Every task
    begins with discharge_evidence.TASK_PREFIX; a workflow whose task does not
    is refused on start and on every read, and reported as "not_found".
  * A workflow id must be a real UUID before it goes into a web address.
  * Only an allowlisted view of each workflow is returned.

ERROR-HANDLING CONTRACT (like mcp_client.py, but as a result, not exceptions):
  * Every function returns a small plain dict with an "outcome" word: ok,
    disabled, not_found, invalid_state, rejected, timeout or unavailable
    (find_latest adds "none" when the admission has no review yet). The
    workflow view is under "workflow" when the outcome is ok.
  * Every message is fixed text: no traceback, server address, server reply
    text or evidence ever appears in a returned message or a log line.
  * One log line per call: the outcome, the workflow status word, the duration.
  * The on/off switch, URL and timeout come from settings.py and are read on
    every call. When the integration is off, nothing is contacted.
  * No retries and no caching.
"""

import logging
import time
import uuid
from urllib.parse import urlsplit

import requests

import settings
from services.discharge_evidence import EVIDENCE_TYPE, TASK_PREFIX

# ------------------------------------------------------------
# Outcome words
# ------------------------------------------------------------
OK = "ok"
DISABLED = "disabled"
NOT_FOUND = "not_found"
INVALID_STATE = "invalid_state"
REJECTED = "rejected"
TIMEOUT = "timeout"
UNAVAILABLE = "unavailable"
NONE = "none"  # find_latest only: the admission has no review yet

_MESSAGES = {
    DISABLED: "Multi-Agent integration is disabled.",
    NOT_FOUND: "Workflow not found.",
    INVALID_STATE: "The workflow is not in a state that allows this.",
    REJECTED: "The request was not accepted.",
    TIMEOUT: "Multi-Agent Server timed out.",
    UNAVAILABLE: "Multi-Agent Server is unavailable.",
    NONE: "No discharge review has been started for this admission.",
}

# 200, 201 and 202 (accepted: the agents run in the background) all succeed.
_SUCCESS_STATUSES = (200, 201, 202)
_STATUS_OUTCOMES = {404: NOT_FOUND, 409: INVALID_STATE, 400: REJECTED}

DECISIONS = ("approve", "correct", "reject", "partially_accept")
STAGES = ("planner", "worker", "reviewer")
HISTORY_PATH = "current_state.admission.admission_id"

logger = logging.getLogger("student2.multi_agent")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)


class _NotOurs(Exception):
    """A workflow whose task does not start with TASK_PREFIX."""


class _BadReply(Exception):
    """The server answered, but not with the documented workflow view."""


# ------------------------------------------------------------
# Small checks
# ------------------------------------------------------------
def _url_is_usable(url):
    """Only plain http(s) URLs with a host and no embedded credentials."""
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def _canonical_id(value):
    """The lower-case UUID text for `value`, or None if it is not a real UUID."""
    try:
        text = str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None
    return text if text == value.lower() else None


def _is_admission_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _admission_of(evidence):
    """current_state.admission.admission_id of an evidence dict, if it is an int."""
    state = evidence.get("current_state") if isinstance(evidence, dict) else None
    admission = state.get("admission") if isinstance(state, dict) else None
    value = admission.get("admission_id") if isinstance(admission, dict) else None
    return value if _is_admission_id(value) else None


def _evidence_fits(admission_id, evidence):
    """The evidence must be ours and name this admission, or find_latest could never find it."""
    return (
        isinstance(evidence, dict)
        and evidence.get("evidence_type") == EVIDENCE_TYPE
        and _admission_of(evidence) == admission_id
    )


# ------------------------------------------------------------
# The workflow view (allowlisted)
# ------------------------------------------------------------
def _stage(stage):
    if not isinstance(stage, dict):
        return None
    execution = stage.get("execution") if isinstance(stage.get("execution"), dict) else {}
    output = stage.get("output")
    return {
        "output": output if isinstance(output, dict) else None,
        "provider": execution.get("provider"),
        "model": execution.get("model"),
        "fallback_used": bool(execution.get("fallback_used")),
        "started_at": stage.get("started_at"),
        "completed_at": stage.get("completed_at"),
    }


def _human_review(review):
    if not isinstance(review, dict):
        return None
    return {key: review.get(key) for key in ("decision", "comments", "timestamp")}


def _project(body):
    """Validate the documented workflow view and keep only the fields we show."""
    if not isinstance(body, dict):
        raise _BadReply
    task = body.get("task")
    if not isinstance(task, str) or not task.startswith(TASK_PREFIX):
        raise _NotOurs
    workflow_id = _canonical_id(body.get("workflow_id"))
    if workflow_id is None or not isinstance(body.get("status"), str):
        raise _BadReply
    evidence = body.get("submitted_evidence")
    return {
        "workflow_id": workflow_id,
        "status": body["status"],
        "task": task,
        # Only filled when the evidence came back (get / find_latest).
        "admission_id": _admission_of(evidence),
        "submitted_evidence": evidence if isinstance(evidence, dict) else None,
        **{name: _stage(body.get(name)) for name in STAGES},
        "human_review": _human_review(body.get("human_review")),
        "final_result": body.get("final_result") if isinstance(body.get("final_result"), dict) else None,
    }


# ------------------------------------------------------------
# One HTTP call
# ------------------------------------------------------------
def _send(method, path, payload=None, params=None):
    """
    Make one request. Returns (outcome, status_code, body): `outcome` is set
    only when no usable reply arrived (timeout / unavailable).
    """
    base = settings.MULTI_AGENT_SERVER_URL()
    if not _url_is_usable(base):
        return UNAVAILABLE, None, None
    try:
        response = requests.request(
            method,
            base + path,
            json=payload,
            params=params,
            timeout=settings.MULTI_AGENT_TIMEOUT(),
            allow_redirects=False,
        )
    except requests.Timeout:
        return TIMEOUT, None, None
    except requests.RequestException:
        return UNAVAILABLE, None, None
    try:
        body = response.json()
    except ValueError:
        body = None
    return None, response.status_code, body


def _workflow_request(method, path, payload=None, params=None):
    """One call that should return a workflow view -> (outcome, workflow)."""
    outcome, status, body = _send(method, path, payload, params)
    if outcome is not None:
        return outcome, None
    if status not in _SUCCESS_STATUSES:
        return _STATUS_OUTCOMES.get(status, UNAVAILABLE), None
    try:
        return OK, _project(body)
    except _NotOurs:
        return NOT_FOUND, None
    except _BadReply:
        return UNAVAILABLE, None


def _fetch(workflow_id):
    """Read one workflow with its evidence -> (outcome, workflow). Never logs."""
    canonical = _canonical_id(workflow_id)
    if canonical is None:
        return NOT_FOUND, None  # not a UUID: nothing to look up
    return _workflow_request(
        "GET", "/workflows/{}".format(canonical), params={"include_evidence": "true"}
    )


# ------------------------------------------------------------
# Result and logging
# ------------------------------------------------------------
def _call(action, run):
    """Check the switch, run `run()` -> (outcome, workflow), log one line, build the result."""
    started = time.perf_counter()
    if settings.MULTI_AGENT_ENABLED():
        outcome, workflow = run()
    else:
        outcome, workflow = DISABLED, None  # nothing is contacted
    duration_ms = round((time.perf_counter() - started) * 1000)
    status = workflow["status"] if workflow else "-"
    logger.info("[MULTI-AGENT] %s outcome=%s status=%s duration_ms=%s", action, outcome, status, duration_ms)

    result = {"outcome": outcome, "ok": outcome == OK}
    if outcome in _MESSAGES:
        result["message"] = _MESSAGES[outcome]
    if workflow is not None:
        result["workflow"] = workflow
    return result


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------
def start(admission_id, task, evidence):
    """
    Start a discharge review. The server accepts it at once ("in_progress") and
    runs the agents in the background; poll get() until "awaiting_human_review".

    Nothing is sent unless `task` starts with TASK_PREFIX and the evidence is
    ours and names `admission_id` (otherwise "rejected").
    """
    def run():
        if (
            not _is_admission_id(admission_id)
            or not isinstance(task, str)
            or not task.startswith(TASK_PREFIX)
            or not _evidence_fits(admission_id, evidence)
        ):
            return REJECTED, None
        return _workflow_request("POST", "/workflows", {"task": task, "evidence": evidence})

    return _call("start", run)


def get(workflow_id):
    """Read one workflow of ours, including the evidence it was started with."""
    return _call("get", lambda: _fetch(workflow_id))


def review(workflow_id, decision, comment):
    """
    Record the assigned doctor's decision. The workflow is read first, so a
    workflow that is not ours is refused before anything is written.
    """
    def run():
        text = comment.strip() if isinstance(comment, str) else None
        if decision not in DECISIONS or text is None or (decision == "correct" and not text):
            return REJECTED, None
        outcome, _ = _fetch(workflow_id)
        if outcome != OK:
            return outcome, None
        return _workflow_request(
            "POST",
            "/workflows/{}/review".format(_canonical_id(workflow_id)),
            {"decision": decision, "comment": text},
        )

    return _call("review", run)


def find_latest(admission_id):
    """
    The newest review of one admission, as a full workflow of ours; outcome
    "none" if the admission has no review yet.
    """
    def run():
        if not _is_admission_id(admission_id):
            return REJECTED, None
        outcome, status, body = _send("GET", "/workflows", params={
            "evidence_type": EVIDENCE_TYPE,
            "evidence_path": HISTORY_PATH,
            "evidence_value": str(admission_id),
            "limit": 1,
        })
        if outcome is not None:
            return outcome, None
        if status != 200:
            return _STATUS_OUTCOMES.get(status, UNAVAILABLE), None
        listed = body.get("workflows") if isinstance(body, dict) else None
        if not isinstance(listed, list):
            return UNAVAILABLE, None
        if not listed:
            return NONE, None
        newest = listed[0].get("workflow_id") if isinstance(listed[0], dict) else None
        if _canonical_id(newest) is None:
            return UNAVAILABLE, None

        # The listing is only a pointer: fetch the full workflow and re-check
        # it is ours (task prefix) and really about this admission.
        outcome, workflow = _fetch(newest)
        if outcome == OK and workflow["admission_id"] != admission_id:
            return NOT_FOUND, None
        return outcome, workflow

    return _call("find_latest", run)


def get_status():
    """
    Report the integration state for the UI / CI. Never raises.

    Returns {"outcome", "enabled", "reachable"} (plus a fixed "message" when not
    ok). "disabled" means the switch is off and nothing was contacted.
    """
    started = time.perf_counter()
    enabled = settings.MULTI_AGENT_ENABLED()
    if not enabled:
        outcome = DISABLED
    else:
        outcome, status, body = _send("GET", "/health")
        if outcome is None:
            healthy = status == 200 and isinstance(body, dict) and body.get("status") == "ok"
            outcome = OK if healthy else UNAVAILABLE
    duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info("[MULTI-AGENT] get_status outcome=%s status=- duration_ms=%s", outcome, duration_ms)

    result = {"outcome": outcome, "enabled": enabled, "reachable": outcome == OK}
    if outcome in _MESSAGES:
        result["message"] = _MESSAGES[outcome]
    return result
