"""Narrow HTTP adapter from Student 5 to the shared Multi-Agent Server.

The adapter transports generic task/evidence payloads and validates the shared
workflow envelope.  Planner, Worker, Reviewer, Human Review, persistence, and
workflow identifiers remain owned by the shared service.
"""

from __future__ import annotations

import json
import math
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
from uuid import UUID

from config import Config


SCHEMA_VERSION = "1.0"
WORKFLOWS_PATH = "/workflows"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TASK_LENGTH = 2_000
MAX_COMMENT_LENGTH = 10_000
WORKFLOW_FIELDS = {
    "schema_version", "workflow_id", "status", "task", "planner", "worker",
    "reviewer", "human_review", "final_result",
}
WORKFLOW_STATUSES = {
    "in_progress", "awaiting_human_review", "approved", "corrected",
    "rejected", "partially_accepted", "failed",
}
FINAL_STATUS_DECISIONS = {
    "approved": "approve",
    "corrected": "correct",
    "rejected": "reject",
    "partially_accepted": "partially_accept",
}
REVIEW_DECISION_VALUES = (
    "approve", "correct", "reject", "partially_accept",
)
REVIEW_DECISIONS = set(REVIEW_DECISION_VALUES)


class MultiAgentClientError(Exception):
    """Base class for safe, classified adapter failures."""


class MultiAgentUnavailable(MultiAgentClientError):
    pass


class MultiAgentTimeout(MultiAgentClientError):
    pass


class MultiAgentHTTPError(MultiAgentClientError):
    pass


class MultiAgentInputError(MultiAgentClientError):
    """Student 5 supplied an invalid workflow identifier or review body."""


class MultiAgentNotFound(MultiAgentClientError):
    """The shared service has no audit record for the workflow identifier."""


class MultiAgentConflict(MultiAgentClientError):
    """The workflow is not in a lifecycle state that accepts Human Review."""


class MultiAgentInvalidResponse(MultiAgentClientError):
    pass


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _canonical_workflow_id(value: Any) -> str:
    if not isinstance(value, str):
        raise MultiAgentInvalidResponse()
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise MultiAgentInvalidResponse() from error
    if str(parsed) != value.lower():
        raise MultiAgentInvalidResponse()
    return str(parsed)


def _validated_workflow_id(value: Any) -> str:
    """Validate caller input separately from an invalid upstream response."""

    try:
        return _canonical_workflow_id(value)
    except MultiAgentInvalidResponse as error:
        raise MultiAgentInputError() from error


def _shared_error_code(error: urllib.error.HTTPError) -> Optional[str]:
    """Read only the bounded public error code from a shared API response."""

    try:
        raw = error.read(MAX_RESPONSE_BYTES + 1)
    except (AttributeError, OSError, ValueError):
        return None
    if len(raw) > MAX_RESPONSE_BYTES:
        return None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(body, dict)
        or body.get("schema_version") != SCHEMA_VERSION
        or not isinstance(body.get("error"), dict)
        or not _nonblank(body["error"].get("message"))
    ):
        return None
    code = body["error"].get("code")
    return code if _nonblank(code) else None


def _validated_stage(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "started_at", "completed_at", "output", "execution",
    }:
        raise MultiAgentInvalidResponse()
    if not _nonblank(value.get("started_at")) or not _nonblank(
            value.get("completed_at")):
        raise MultiAgentInvalidResponse()
    if not isinstance(value.get("output"), dict):
        raise MultiAgentInvalidResponse()
    execution = value.get("execution")
    if execution is not None and not isinstance(execution, dict):
        raise MultiAgentInvalidResponse()
    if isinstance(execution, dict) and not set(execution) <= {
        "provider", "model", "fallback_used", "fallback_reason",
        "attempt_count", "error",
    }:
        raise MultiAgentInvalidResponse()


def _validated_human_review(value: Any, expected_decision: str) -> None:
    if not isinstance(value, dict) or set(value) != {
        "decision", "comments", "timestamp",
    }:
        raise MultiAgentInvalidResponse()
    if (
        value.get("decision") != expected_decision
        or not isinstance(value.get("comments"), str)
        or not _nonblank(value.get("timestamp"))
    ):
        raise MultiAgentInvalidResponse()


def _validated_final_result(
    value: Any, status: str, expected_decision: str, comments: str
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "status", "proposal", "review", "human_decision",
        "human_comments_or_correction", "mutation_executed",
    }:
        raise MultiAgentInvalidResponse()
    if (
        value.get("status") != status
        or value.get("human_decision") != expected_decision
        or value.get("human_comments_or_correction") != comments
        or value.get("mutation_executed") is not False
        or not isinstance(value.get("review"), dict)
    ):
        raise MultiAgentInvalidResponse()


def _validated_workflow(
    body: Any,
    *,
    expected_task: Optional[str] = None,
    expected_status: Optional[str] = None,
    require_completed_stages: bool = False,
) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) != WORKFLOW_FIELDS:
        raise MultiAgentInvalidResponse()
    if body.get("schema_version") != SCHEMA_VERSION:
        raise MultiAgentInvalidResponse()
    _canonical_workflow_id(body.get("workflow_id"))
    if body.get("status") not in WORKFLOW_STATUSES or not _nonblank(body.get("task")):
        raise MultiAgentInvalidResponse()
    if expected_task is not None and body["task"] != expected_task:
        raise MultiAgentInvalidResponse()
    if expected_status is not None and body["status"] != expected_status:
        raise MultiAgentInvalidResponse()

    for role in ("planner", "worker", "reviewer"):
        stage = body.get(role)
        if stage is None and not require_completed_stages:
            continue
        _validated_stage(stage)
    status = body["status"]
    if status in {"in_progress", "awaiting_human_review"}:
        if body["human_review"] is not None or body["final_result"] is not None:
            raise MultiAgentInvalidResponse()
        if status == "awaiting_human_review":
            for role in ("planner", "worker", "reviewer"):
                _validated_stage(body[role])
    elif status in FINAL_STATUS_DECISIONS:
        expected_decision = FINAL_STATUS_DECISIONS[status]
        for role in ("planner", "worker", "reviewer"):
            _validated_stage(body[role])
        _validated_human_review(body["human_review"], expected_decision)
        _validated_final_result(
            body["final_result"],
            status,
            expected_decision,
            body["human_review"]["comments"],
        )
    elif status == "failed":
        final_result = body["final_result"]
        if (
            body["human_review"] is not None
            or not isinstance(final_result, dict)
            or final_result.get("status") != "failed"
            or final_result.get("mutation_executed") is not False
        ):
            raise MultiAgentInvalidResponse()
    return body


class MultiAgentClient:
    """Bounded HTTP adapter for creating, polling, and reviewing workflows."""

    def __init__(self, base_url: str = Config.MULTI_AGENT_BASE_URL,
                 timeout: float = Config.MULTI_AGENT_TIMEOUT):
        self.base_url = base_url
        self.timeout = timeout

    def _validated_base_url(self) -> str:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise MultiAgentInvalidResponse()
        return self.base_url.rstrip("/")

    def _request(
        self, path: str, *, method: str, payload: Optional[Dict[str, Any]] = None
    ) -> Any:
        base_url = self._validated_base_url()
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base_url + path,
            data=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code == 504:
                raise MultiAgentTimeout() from error
            if error.code in {502, 503}:
                raise MultiAgentUnavailable() from error
            if error.code == 404:
                if _shared_error_code(error) == "not_found":
                    raise MultiAgentNotFound() from error
                raise MultiAgentInvalidResponse() from error
            if error.code == 409:
                if _shared_error_code(error) == "invalid_workflow_state":
                    raise MultiAgentConflict() from error
                raise MultiAgentInvalidResponse() from error
            raise MultiAgentHTTPError() from error
        except (socket.timeout, TimeoutError) as error:
            raise MultiAgentTimeout() from error
        except urllib.error.URLError as error:
            if isinstance(getattr(error, "reason", None), (socket.timeout, TimeoutError)):
                raise MultiAgentTimeout() from error
            raise MultiAgentUnavailable() from error
        except OSError as error:
            raise MultiAgentUnavailable() from error

        if len(raw) > MAX_RESPONSE_BYTES:
            raise MultiAgentInvalidResponse()
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MultiAgentInvalidResponse() from error

    def start_workflow(self, task: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        if not _nonblank(task) or len(task) > MAX_TASK_LENGTH:
            raise MultiAgentInvalidResponse()
        if not isinstance(evidence, dict) or not evidence:
            raise MultiAgentInvalidResponse()
        body = self._request(
            WORKFLOWS_PATH,
            method="POST",
            payload={"task": task, "evidence": evidence},
        )
        return _validated_workflow(
            body,
            expected_task=task,
            expected_status="in_progress",
        )

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        workflow_id = _validated_workflow_id(workflow_id)
        body = self._request(
            f"{WORKFLOWS_PATH}/{workflow_id}", method="GET")
        return _validated_workflow(body)

    def submit_review(
        self, workflow_id: str, decision: str, comment: str = ""
    ) -> Dict[str, Any]:
        workflow_id = _validated_workflow_id(workflow_id)
        if decision not in REVIEW_DECISIONS or not isinstance(comment, str):
            raise MultiAgentInputError()
        comment = comment.strip()
        if len(comment) > MAX_COMMENT_LENGTH:
            raise MultiAgentInputError()
        if decision == "correct" and not comment:
            raise MultiAgentInputError()
        body = self._request(
            f"{WORKFLOWS_PATH}/{workflow_id}/review",
            method="POST",
            payload={"decision": decision, "comment": comment},
        )
        expected_status = {
            "approve": "approved",
            "correct": "corrected",
            "reject": "rejected",
            "partially_accept": "partially_accepted",
        }[decision]
        return _validated_workflow(
            body,
            expected_status=expected_status,
            require_completed_stages=True,
        )
