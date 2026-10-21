"""Bounded client for the shared, non-containerised HOMS Multi-Agent Server.

Only this module talks to the Multi-Agent Server (ai-services/multi-agent-server,
port 8200). The server owns the Planner -> Worker -> Reviewer agents, the
Human Review gate and the audit history; this client only sends evidence,
reads workflow state and forwards the coordinator's review decision.

Off by default (MULTI_AGENT_ENABLED=false), which is the required cloud
baseline. Every call returns a MultiAgentResult, so a disabled, slow or
missing server never breaks the Room & Bed API.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import socket
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID

MULTI_AGENT_ENABLED = os.environ.get("MULTI_AGENT_ENABLED", "false").strip().lower() == "true"
MULTI_AGENT_SERVER_URL = os.environ.get(
    "MULTI_AGENT_SERVER_URL", "http://127.0.0.1:8200").strip().rstrip("/")
try:
    MULTI_AGENT_TIMEOUT = float(os.environ.get("MULTI_AGENT_TIMEOUT", "30"))
except ValueError:
    MULTI_AGENT_TIMEOUT = 30.0

ALLOWED_SCHEMES = ("http", "https")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_COMMENT_LENGTH = 2_000
DECISIONS = ("approve", "correct", "reject", "partially_accept")
STATUSES = ("in_progress", "awaiting_human_review", "approved", "corrected",
            "rejected", "partially_accepted", "failed")

logger = logging.getLogger("student4.multi_agent")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@dataclass
class MultiAgentResult:
    """``outcome`` is ok, disabled, unavailable, timeout, not_found, conflict,
    invalid_request or invalid_response."""

    ok: bool
    outcome: str
    data: Any = None
    error: str | None = None


class _InvalidResponse(ValueError):
    pass


def valid_workflow_id(value: Any) -> str | None:
    """Return the canonical UUID string, or None when ``value`` is not a workflow id."""
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return str(parsed) if str(parsed) == value.lower() else None


def _server_url(path: str, query: dict | None = None) -> str:
    """The Multi-Agent URL for ``path``; only http(s) to a host is allowed (Ruff S310)."""
    parts = urllib.parse.urlsplit(MULTI_AGENT_SERVER_URL)
    if parts.scheme not in ALLOWED_SCHEMES or not parts.netloc:
        raise urllib.error.URLError("MULTI_AGENT_SERVER_URL must be an http(s) URL")
    return MULTI_AGENT_SERVER_URL + path + ("?" + urllib.parse.urlencode(query) if query else "")


def _request(method: str, path: str, payload: dict | None = None, query: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(  # noqa: S310 - scheme validated by _server_url
        _server_url(path, query), data=data, method=method,
        headers={"Accept": "application/json",
                 **({"Content-Type": "application/json"} if data else {})},
    )
    try:
        with urllib.request.urlopen(call, timeout=MULTI_AGENT_TIMEOUT) as response:  # noqa: S310
            return response.status, _json(response.read(MAX_RESPONSE_BYTES + 1))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, _json(exc.read(MAX_RESPONSE_BYTES + 1))
        except _InvalidResponse:
            return exc.code, {}


def _json(raw: bytes) -> Any:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _InvalidResponse("response too large")
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _InvalidResponse("response is not JSON") from error


def _workflow(body: Any) -> dict:
    """Check the shared workflow view and keep it unchanged."""
    if not isinstance(body, dict) or not valid_workflow_id(body.get("workflow_id")) \
            or body.get("status") not in STATUSES:
        raise _InvalidResponse("not a workflow")
    for stage in ("planner", "worker", "reviewer"):
        if body.get(stage) is not None and not isinstance(body[stage], dict):
            raise _InvalidResponse("{} stage is malformed".format(stage))
    return body


def _error_message(body: Any, status: int) -> str:
    detail = body.get("error") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("message"), str):
        return detail["message"][:300]
    return "Multi-Agent Server returned HTTP {}".format(status)


def _call(name: str, method: str, path: str, *, payload=None, query=None, parse=_workflow,
          expected=(200,)) -> MultiAgentResult:
    if not MULTI_AGENT_ENABLED:
        return MultiAgentResult(False, "disabled", error="Multi-Agent mode is disabled")
    started = time.perf_counter()
    try:
        status, body = _request(method, path, payload, query)
        if status in expected:
            result = MultiAgentResult(True, "ok", parse(body))
        else:
            outcome = {400: "invalid_request", 404: "not_found", 409: "conflict",
                       504: "timeout"}.get(status, "unavailable")
            result = MultiAgentResult(False, outcome, error=_error_message(body, status))
    except (socket.timeout, TimeoutError):
        result = MultiAgentResult(False, "timeout", error="Multi-Agent Server timed out")
    except urllib.error.URLError as exc:
        timed_out = isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError))
        result = MultiAgentResult(False, "timeout" if timed_out else "unavailable",
                                  error="Multi-Agent Server timed out" if timed_out
                                  else "Multi-Agent Server is unavailable")
    except (_InvalidResponse, OSError):
        result = MultiAgentResult(False, "invalid_response",
                                  error="Multi-Agent Server returned an invalid response")
    logger.info("[MULTI-AGENT] %s outcome=%s duration_ms=%s server=%s", name, result.outcome,
                round((time.perf_counter() - started) * 1000), MULTI_AGENT_SERVER_URL)
    return result


def status() -> dict:
    """Non-crashing status for the UI and CI; only contacts the server when enabled."""
    base = {"enabled": MULTI_AGENT_ENABLED, "server_url": MULTI_AGENT_SERVER_URL}
    if not MULTI_AGENT_ENABLED:
        return {**base, "reachable": False}
    result = _call("health", "GET", "/health", parse=lambda body: body)
    reachable = result.ok and isinstance(result.data, dict) and result.data.get("status") == "ok"
    return {**base, "reachable": reachable,
            **({} if reachable else {"error": result.error or "unexpected health response"})}


def start(task: str, evidence: dict) -> MultiAgentResult:
    """Create a workflow; the server returns 202 and runs the agents in the background."""
    return _call("start", "POST", "/workflows", payload={"task": task, "evidence": evidence},
                 expected=(200, 201, 202))


def get(workflow_id: str, *, include_evidence: bool = False) -> MultiAgentResult:
    workflow_id = valid_workflow_id(workflow_id)
    if not workflow_id:
        return MultiAgentResult(False, "invalid_request", error="Invalid workflow id")
    return _call("get", "GET", "/workflows/" + workflow_id,
                 query={"include_evidence": "true"} if include_evidence else None)


def review(workflow_id: str, decision: str, comment: str) -> MultiAgentResult:
    workflow_id = valid_workflow_id(workflow_id)
    if not workflow_id:
        return MultiAgentResult(False, "invalid_request", error="Invalid workflow id")
    return _call("review", "POST", "/workflows/{}/review".format(workflow_id),
                 payload={"decision": decision, "comment": comment})


def history(evidence_type: str, case_id: int, limit: int = 10) -> MultiAgentResult:
    """Recent workflows for one shortage case, newest first as the server returns them."""

    def parse(body):
        if not isinstance(body, dict) or not isinstance(body.get("workflows"), list):
            raise _InvalidResponse("not a workflow list")
        return [{key: item.get(key) for key in ("workflow_id", "status", "started_at", "human_review")}
                for item in body["workflows"] if isinstance(item, dict)]

    return _call("history", "GET", "/workflows", parse=parse, query={
        "evidence_type": evidence_type, "evidence_path": "case_id",
        "evidence_value": str(case_id), "limit": str(limit)})
