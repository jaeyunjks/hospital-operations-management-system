"""Bounded client for the shared, non-containerised HOMS Multi-Agent Server.

Only this module communicates with the Multi-Agent Server. The pharmacy feature
starts one kind of workflow, a reorder decision (Planner -> Worker -> Reviewer ->
Human Review), and may only read or review workflows it started: every task is
prefixed with ``TASK_PREFIX`` and other workflows are refused. Only an
allowlisted view of each workflow is returned to the frontend.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
import socket
import time
from typing import Any
import urllib.error
import urllib.request
import urllib.parse
import uuid

from services.http_url import http_url

TASK_PREFIX = "Pharmacy reorder decision: "
DECISIONS = ("approve", "correct", "partially_accept", "reject")
MAX_COMMENT_LENGTH = 1000
STAGES = ("planner", "worker", "reviewer")

MULTI_AGENT_ENABLED = os.environ.get("MULTI_AGENT_ENABLED", "false").strip().lower() == "true"
MULTI_AGENT_SERVER_URL = http_url(os.environ.get("MULTI_AGENT_SERVER_URL", "http://127.0.0.1:8200"),
                                  "MULTI_AGENT_SERVER_URL")
try:
    MULTI_AGENT_TIMEOUT = float(os.environ.get("MULTI_AGENT_TIMEOUT", "300"))
except ValueError:
    MULTI_AGENT_TIMEOUT = 300.0

logger = logging.getLogger("student3.multi_agent")
if not logger.handlers:
    terminal_handler = logging.StreamHandler()
    terminal_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(terminal_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class InvalidResponse(ValueError):
    """The Multi-Agent Server answered, but not with the documented contract."""


@dataclass
class WorkflowResult:
    """A safe result for every call, including transport failures.

    ``outcome`` is ``ok``, ``disabled``, ``not_found``, ``forbidden``,
    ``invalid_state``, ``rejected``, ``workflow_failed``, ``timeout`` or
    ``unavailable``.
    """

    ok: bool
    outcome: str
    workflow: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(f"{MULTI_AGENT_SERVER_URL}{path}", data=data, method=method,  # noqa: S310 - http(s) base URL checked at start-up
                                  headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(call, timeout=timeout or MULTI_AGENT_TIMEOUT) as response:  # noqa: S310 - http(s) base URL checked by http_url()
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except (json.JSONDecodeError, AttributeError):
            return exc.code, {}


def valid_workflow_id(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _stage(stage: Any) -> dict[str, Any] | None:
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


def _evidence_summary(evidence: Any) -> dict[str, Any] | None:
    """Rebuild the backend's evidence summary from the evidence stored with the workflow."""
    if not isinstance(evidence, dict):
        return None
    # Facts are top-level keys (earlier records nested them under "current_state").
    state = evidence["current_state"] if isinstance(evidence.get("current_state"), dict) else evidence
    if "medicine" not in state:
        return None
    options = evidence.get("options") if isinstance(evidence.get("options"), list) else []
    suggested = next((option.get("outcomes", {}).get("metric_delta") for option in options
                      if isinstance(option, dict) and option.get("id") == "order_suggested"), None)
    return {"medicine_name": state.get("medicine"), "supplier_name": state.get("supplier"),
            "available_quantity": state.get("available_units"),
            "near_expiry_quantity": state.get("near_expiry_units"),
            "open_order_quantity": state.get("open_order_units"),
            "daily_usage_rate": state.get("daily_usage_rate"), "lead_time_days": state.get("lead_time_days"),
            "suggested_quantity": suggested, "unit_price": state.get("unit_price"),
            "mcp_order_alerts": evidence.get("mcp_order_alerts") if isinstance(evidence.get("mcp_order_alerts"), dict) else None,
            "rag_reorder_policy": evidence.get("rag_reorder_policy") if isinstance(evidence.get("rag_reorder_policy"), dict) else None}


def _project(body: Any) -> dict[str, Any]:
    """Validate the documented workflow view and keep only display fields."""
    if not isinstance(body, dict) or not valid_workflow_id(body.get("workflow_id")):
        raise InvalidResponse("workflow_id is missing")
    task = body.get("task")
    if not isinstance(task, str) or not task.startswith(TASK_PREFIX):
        raise PermissionError("workflow was not started by the pharmacy feature")
    if not isinstance(body.get("status"), str):
        raise InvalidResponse("status is missing")
    review = body.get("human_review") if isinstance(body.get("human_review"), dict) else None
    return {
        "workflow_id": body["workflow_id"],
        "status": body["status"],
        "task": task,
        "medicine": task[len(TASK_PREFIX):],
        **{name: _stage(body.get(name)) for name in STAGES},
        "human_review": review,
        "final_result": body.get("final_result") if isinstance(body.get("final_result"), dict) else None,
        "evidence_summary": _evidence_summary(body.get("submitted_evidence")),
    }


def _call(action: str, method: str, path: str, payload: dict[str, Any] | None = None,
          timeout: float | None = None) -> WorkflowResult:
    if not MULTI_AGENT_ENABLED:
        return WorkflowResult(False, "disabled", error="Multi-Agent mode is disabled")
    started = time.perf_counter()
    try:
        status, body = _request(method, path, payload, timeout)
        if status in (200, 201, 202):  # 202: accepted; the agents run in the background
            result = WorkflowResult(True, "ok", workflow=_project(body))
        else:
            detail = body.get("error", {}) if isinstance(body, dict) else {}
            code = detail.get("code", f"http_{status}") if isinstance(detail, dict) else f"http_{status}"
            message = detail.get("message") if isinstance(detail, dict) else None
            outcome = {404: "not_found", 409: "invalid_state", 400: "rejected", 503: "workflow_failed",
                       504: "timeout"}.get(status, "unavailable")
            result = WorkflowResult(False, outcome, error=f"Multi-Agent Server could not complete the request ({code})"
                                    + (f": {message}" if message else ""))
            failed = body.get("workflow") if isinstance(body, dict) else None
            if failed is not None:
                try:
                    result.workflow = _project(failed)
                except (InvalidResponse, PermissionError):
                    pass
    except PermissionError:
        result = WorkflowResult(False, "forbidden", error="This workflow does not belong to the pharmacy feature")
    except (socket.timeout, TimeoutError):
        result = WorkflowResult(False, "timeout", error="Multi-Agent Server timed out")
    except urllib.error.URLError as exc:
        timed_out = isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError))
        result = WorkflowResult(False, "timeout" if timed_out else "unavailable",
                                error="Multi-Agent Server timed out" if timed_out
                                else "Multi-Agent Server is unavailable")
    except (json.JSONDecodeError, InvalidResponse, OSError):
        result = WorkflowResult(False, "unavailable", error="Multi-Agent Server returned an invalid response")
    result.duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info("[MULTI-AGENT] %s outcome=%s status=%s duration_ms=%s server=%s", action, result.outcome,
                (result.workflow or {}).get("status", "-"), result.duration_ms, MULTI_AGENT_SERVER_URL)
    return result


def start(medicine_name: str, evidence: dict[str, Any]) -> WorkflowResult:
    """Start a workflow. The server accepts it at once (``in_progress``) and runs Planner,
    Worker and Reviewer in the background; poll :func:`get` until ``awaiting_human_review``."""
    return _call("start", "POST", "/workflows", {"task": TASK_PREFIX + medicine_name, "evidence": evidence},
                 timeout=30)


def get(workflow_id: str) -> WorkflowResult:
    """Read one pharmacy workflow, including the evidence it was started with."""
    return _call("get", "GET", f"/workflows/{workflow_id}?include_evidence=true", timeout=10)


HISTORY_EVIDENCE_TYPE = "student3_reorder_decision"
MAX_HISTORY = 20


def history(limit: int = 10) -> dict[str, Any]:
    """Recent pharmacy reorder reviews, newest first, with their medicine and human decision."""
    if not MULTI_AGENT_ENABLED:
        return {"ok": False, "outcome": "disabled", "error": "Multi-Agent mode is disabled", "reviews": []}
    query = urllib.parse.urlencode({"evidence_type": HISTORY_EVIDENCE_TYPE, "limit": max(1, min(limit, MAX_HISTORY))})
    try:
        status, body = _request("GET", f"/workflows?{query}", timeout=10)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {"ok": False, "outcome": "unavailable", "error": "Multi-Agent Server is unavailable", "reviews": []}
    listed = body.get("workflows") if status == 200 and isinstance(body, dict) else None
    if not isinstance(listed, list):
        return {"ok": False, "outcome": "unavailable", "error": "Multi-Agent Server returned an invalid response",
                "reviews": []}
    reviews = []
    for item in listed:
        workflow_id = item.get("workflow_id") if isinstance(item, dict) else None
        if not valid_workflow_id(workflow_id):
            continue
        detail = get(workflow_id)  # also confirms the workflow is a pharmacy one
        if not detail.ok:
            continue
        review = detail.workflow["human_review"] or {}
        reviews.append({"workflow_id": workflow_id, "medicine": detail.workflow["medicine"],
                        "status": detail.workflow["status"], "started_at": item.get("started_at"),
                        "decision": review.get("decision"), "comment": review.get("comments")})
    return {"ok": True, "outcome": "ok", "reviews": reviews}


def review(workflow_id: str, decision: str, comment: str) -> WorkflowResult:
    """Record the Pharmacy Manager's decision, after checking the workflow is the pharmacy's."""
    current = get(workflow_id)
    if not current.ok:
        return current
    return _call("review", "POST", f"/workflows/{workflow_id}/review",
                 {"decision": decision, "comment": comment}, timeout=30)


def status() -> dict[str, Any]:
    """Non-crashing status for the UI and CI; only contacts the server when enabled."""
    base = {"enabled": MULTI_AGENT_ENABLED, "server_url": MULTI_AGENT_SERVER_URL}
    if not MULTI_AGENT_ENABLED:
        return {**base, "reachable": False}
    try:
        code, body = _request("GET", "/health", timeout=5)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {**base, "reachable": False, "error": "Multi-Agent Server is unavailable"}
    return {**base, "reachable": code == 200 and isinstance(body, dict) and body.get("status") == "ok"}
