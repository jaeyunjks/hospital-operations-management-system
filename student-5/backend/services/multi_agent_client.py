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
WORKFLOW_FIELDS = {
    "schema_version", "workflow_id", "status", "task", "planner", "worker",
    "reviewer", "human_review", "final_result",
}
WORKFLOW_STATUSES = {
    "in_progress", "awaiting_human_review", "approved", "corrected",
    "rejected", "partially_accepted", "failed",
}
REVIEW_DECISIONS = {"approve", "correct", "reject", "partially_accept"}


class MultiAgentClientError(Exception):
    """Base class for safe, classified adapter failures."""


class MultiAgentUnavailable(MultiAgentClientError):
    pass


class MultiAgentTimeout(MultiAgentClientError):
    pass


class MultiAgentHTTPError(MultiAgentClientError):
    pass


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


def _validated_stage(value: Any) -> None:
    if not isinstance(value, dict):
        raise MultiAgentInvalidResponse()
    if not isinstance(value.get("output"), dict):
        raise MultiAgentInvalidResponse()
    execution = value.get("execution")
    if execution is not None and not isinstance(execution, dict):
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
    if body.get("human_review") is not None and not isinstance(
            body["human_review"], dict):
        raise MultiAgentInvalidResponse()
    if body.get("final_result") is not None and not isinstance(
            body["final_result"], dict):
        raise MultiAgentInvalidResponse()

    if expected_status == "awaiting_human_review" and (
        body["human_review"] is not None or body["final_result"] is not None
    ):
        raise MultiAgentInvalidResponse()
    return body


class MultiAgentClient:
    """Synchronous, bounded adapter for the shared workflow lifecycle."""

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
            expected_status="awaiting_human_review",
            require_completed_stages=True,
        )

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        workflow_id = _canonical_workflow_id(workflow_id)
        body = self._request(
            f"{WORKFLOWS_PATH}/{workflow_id}", method="GET")
        return _validated_workflow(body)

    def submit_review(
        self, workflow_id: str, decision: str, comment: str = ""
    ) -> Dict[str, Any]:
        workflow_id = _canonical_workflow_id(workflow_id)
        if decision not in REVIEW_DECISIONS or not isinstance(comment, str):
            raise MultiAgentInvalidResponse()
        comment = comment.strip()
        if decision == "correct" and not comment:
            raise MultiAgentInvalidResponse()
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
