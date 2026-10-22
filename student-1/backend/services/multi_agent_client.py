"""Bounded HTTP client for the shared, host-local Multi-Agent Server."""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import requests


class MultiAgentError(Exception):
    status_code = 502
    code = "multi_agent_error"


class MultiAgentDisabled(MultiAgentError):
    status_code = 503
    code = "multi_agent_disabled"


class MultiAgentUnavailable(MultiAgentError):
    status_code = 503
    code = "multi_agent_unavailable"


class MultiAgentTimeout(MultiAgentError):
    status_code = 504
    code = "multi_agent_timeout"


class MultiAgentInvalidResponse(MultiAgentError):
    status_code = 502
    code = "multi_agent_invalid_response"


class MultiAgentWorkflowConflict(MultiAgentError):
    status_code = 409
    code = "multi_agent_workflow_conflict"


class MultiAgentClient:
    def __init__(self, enabled: bool, server_url: str, timeout: float):
        self.enabled = enabled
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout

    def _base_url(self) -> str:
        if not self.enabled:
            raise MultiAgentDisabled()
        try:
            parsed = urlsplit(self.server_url)
            valid_url = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid_url = False
        if (
            not valid_url
            or isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise MultiAgentInvalidResponse()
        return self.server_url

    @staticmethod
    def _workflow(body: Any) -> dict[str, Any]:
        required = {
            "schema_version", "workflow_id", "status", "task", "planner",
            "worker", "reviewer", "human_review", "final_result",
        }
        allowed = required | {"submitted_evidence"}
        if (
            not isinstance(body, dict)
            or not required.issubset(body)
            or set(body) - allowed
            or body.get("schema_version") != "1.0"
            or body.get("status") not in {
                "in_progress", "awaiting_human_review", "approved",
                "corrected", "partially_accepted", "rejected", "failed",
            }
            or not isinstance(body.get("task"), str)
        ):
            raise MultiAgentInvalidResponse()
        try:
            if str(UUID(body["workflow_id"])) != body["workflow_id"].lower():
                raise ValueError()
        except (ValueError, TypeError, AttributeError) as error:
            raise MultiAgentInvalidResponse() from error
        for stage in ("planner", "worker", "reviewer"):
            if body[stage] is not None and not isinstance(body[stage], dict):
                raise MultiAgentInvalidResponse()
        if (
            "submitted_evidence" in body
            and not isinstance(body["submitted_evidence"], dict)
        ):
            raise MultiAgentInvalidResponse()
        return body

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        base_url = self._base_url()
        try:
            response = requests.request(
                method,
                f"{base_url}{path}",
                timeout=self.timeout,
                headers={"Accept": "application/json"},
                **kwargs,
            )
        except requests.Timeout as error:
            raise MultiAgentTimeout() from error
        except requests.RequestException as error:
            raise MultiAgentUnavailable() from error

        try:
            body = response.json()
        except ValueError as error:
            raise MultiAgentInvalidResponse() from error
        if response.status_code == 409:
            raise MultiAgentWorkflowConflict()
        if response.status_code >= 500:
            raise MultiAgentUnavailable()
        if response.status_code >= 400:
            raise MultiAgentInvalidResponse()
        return self._workflow(body)

    def create_workflow(self, task: str, evidence: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/workflows", json={"task": task, "evidence": evidence}
        )

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        try:
            validated_id = str(UUID(workflow_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise MultiAgentInvalidResponse() from error
        return self._request(
            "GET", f"/workflows/{validated_id}?include_evidence=true"
        )

    def submit_review(
        self, workflow_id: str, decision: str, comment: str
    ) -> dict[str, Any]:
        try:
            validated_id = str(UUID(workflow_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise MultiAgentInvalidResponse() from error
        return self._request(
            "POST",
            f"/workflows/{validated_id}/review",
            json={"decision": decision, "comment": comment},
        )
