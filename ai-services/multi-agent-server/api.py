#!/usr/bin/env python3
"""Local HTTP interface for the shared Release 2 Multi-Agent workflow."""

from __future__ import annotations

import argparse
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Sequence
from uuid import UUID

from flask import Flask, jsonify, request

from audit import AuditNotFoundError
from main import DEFAULT_REPORTS_DIR, add_model_arguments, build_orchestrator
from models import HumanDecision, HumanReview, SCHEMA_VERSION, utc_now
from orchestrator import InvalidWorkflowStateError, MultiAgentOrchestrator


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8200
MAX_BODY_BYTES = 256 * 1024
MAX_TASK_CHARS = 2_000
MAX_COMMENT_CHARS = 10_000
MAX_EVIDENCE_TYPE_CHARS = 120
MAX_HISTORY_RESULTS = 100
MAX_EVIDENCE_PATH_CHARS = 160
WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]", "*"}
WORKFLOW_FIELDS = {"task", "evidence"}
REVIEW_FIELDS = {"decision", "comment"}


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    allow_docker_host: bool


class ApiError(RuntimeError):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def load_server_config(environment=None) -> ServerConfig:
    """Load loopback-only HTTP settings unless Docker-host access is explicit."""

    environment = os.environ if environment is None else environment
    host = environment.get("HOMS_MULTI_AGENT_HOST", DEFAULT_HOST).strip()
    if not host:
        raise ValueError("HOMS_MULTI_AGENT_HOST must not be blank")
    try:
        port = int(environment.get("HOMS_MULTI_AGENT_PORT", str(DEFAULT_PORT)))
    except ValueError as error:
        raise ValueError("HOMS_MULTI_AGENT_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("HOMS_MULTI_AGENT_PORT must be between 1 and 65535")
    raw_allow = environment.get(
        "HOMS_MULTI_AGENT_ALLOW_DOCKER_HOST", "false"
    ).strip().lower()
    if raw_allow not in {"true", "false"}:
        raise ValueError(
            "HOMS_MULTI_AGENT_ALLOW_DOCKER_HOST must be 'true' or 'false'"
        )
    allow_docker_host = raw_allow == "true"
    if host in WILDCARD_HOSTS and not allow_docker_host:
        raise ValueError(
            "HOMS_MULTI_AGENT_ALLOW_DOCKER_HOST must be true for a wildcard bind"
        )
    return ServerConfig(host=host, port=port, allow_docker_host=allow_docker_host)


def allowed_hosts(config: ServerConfig) -> set[str]:
    hosts = {
        f"127.0.0.1:{config.port}",
        f"localhost:{config.port}",
        f"[::1]:{config.port}",
    }
    if config.allow_docker_host:
        hosts.add(f"host.docker.internal:{config.port}")
    return hosts


def _error(code: str, message: str, status: int):
    return (
        jsonify(
            {
                "schema_version": SCHEMA_VERSION,
                "error": {"code": code, "message": message},
            }
        ),
        status,
    )


def _json_object(allowed_fields: set[str]) -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError("validation_error", "Request body must be a JSON object", 400)
    unexpected = sorted(set(payload) - allowed_fields)
    if unexpected:
        raise ApiError(
            "validation_error",
            f"Unexpected field(s): {', '.join(unexpected)}",
            400,
        )
    return payload


def _validated_workflow_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ApiError("not_found", "Workflow not found", 404) from error
    if str(parsed) != value.lower():
        raise ApiError("not_found", "Workflow not found", 404)
    return str(parsed)


def _execution_view(execution: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(execution, dict):
        return None
    fields = (
        "provider",
        "model",
        "fallback_used",
        "fallback_reason",
        "attempt_count",
        "error",
    )
    return {field: execution.get(field) for field in fields if field in execution}


def _stage_view(stage: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(stage, dict):
        return None
    return {
        "started_at": stage.get("started_at"),
        "completed_at": stage.get("completed_at"),
        "output": stage.get("output"),
        "execution": _execution_view(stage.get("execution")),
    }


def workflow_view(
    record: Dict[str, Any], *, include_evidence: bool = False
) -> Dict[str, Any]:
    """Return the stable public view while the audit retains complete internals."""

    view = {
        "schema_version": record.get("schema_version", SCHEMA_VERSION),
        "workflow_id": record["workflow_id"],
        "status": record["final_status"],
        "task": record["task"],
        "planner": _stage_view(record.get("planner")),
        "worker": _stage_view(record.get("worker")),
        "reviewer": _stage_view(record.get("reviewer")),
        "human_review": record.get("human_review"),
        "final_result": record.get("final_result"),
    }
    if include_evidence:
        view["submitted_evidence"] = record.get("input_evidence")
    return view


def _include_evidence_requested() -> bool:
    unexpected = sorted(set(request.args) - {"include_evidence"})
    if unexpected:
        raise ApiError(
            "validation_error",
            f"Unexpected query field(s): {', '.join(unexpected)}",
            400,
        )
    value = request.args.get("include_evidence", "false").strip().lower()
    if value not in {"true", "false"}:
        raise ApiError(
            "validation_error", "include_evidence must be 'true' or 'false'", 400
        )
    return value == "true"


def _history_query() -> tuple[str, int, Optional[str], Optional[str]]:
    unexpected = sorted(
        set(request.args)
        - {"evidence_type", "limit", "evidence_path", "evidence_value"}
    )
    if unexpected:
        raise ApiError(
            "validation_error",
            f"Unexpected query field(s): {', '.join(unexpected)}",
            400,
        )
    evidence_type = request.args.get("evidence_type", "").strip()
    if not evidence_type or len(evidence_type) > MAX_EVIDENCE_TYPE_CHARS:
        raise ApiError(
            "validation_error",
            f"evidence_type must be 1-{MAX_EVIDENCE_TYPE_CHARS} characters",
            400,
        )
    try:
        limit = int(request.args.get("limit", "10"))
    except ValueError as error:
        raise ApiError("validation_error", "limit must be an integer", 400) from error
    if not 1 <= limit <= MAX_HISTORY_RESULTS:
        raise ApiError(
            "validation_error",
            f"limit must be between 1 and {MAX_HISTORY_RESULTS}",
            400,
        )
    evidence_path = request.args.get("evidence_path")
    evidence_value = request.args.get("evidence_value")
    if (evidence_path is None) != (evidence_value is None):
        raise ApiError(
            "validation_error",
            "evidence_path and evidence_value must be supplied together",
            400,
        )
    if evidence_path is not None:
        evidence_path = evidence_path.strip()
        if (
            not evidence_path
            or len(evidence_path) > MAX_EVIDENCE_PATH_CHARS
            or re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", evidence_path)
            is None
            or len(evidence_value) > MAX_EVIDENCE_TYPE_CHARS
        ):
            raise ApiError(
                "validation_error", "evidence filter is invalid", 400
            )
    return evidence_type, limit, evidence_path, evidence_value


def workflow_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    """Return only fields needed to discover and reopen persisted workflows."""

    return {
        "workflow_id": record["workflow_id"],
        "status": record["final_status"],
        "started_at": record.get("timestamp"),
        "human_review": record.get("human_review"),
    }


def _workflow_request() -> tuple[str, Dict[str, Any]]:
    payload = _json_object(WORKFLOW_FIELDS)
    task = payload.get("task")
    evidence = payload.get("evidence")
    if not isinstance(task, str) or not task.strip():
        raise ApiError("validation_error", "task must be a non-blank string", 400)
    if len(task) > MAX_TASK_CHARS:
        raise ApiError(
            "validation_error",
            f"task must be at most {MAX_TASK_CHARS} characters",
            400,
        )
    if not isinstance(evidence, dict) or not evidence:
        raise ApiError(
            "validation_error", "evidence must be a non-empty JSON object", 400
        )
    return task.strip(), evidence


def _human_review_request() -> HumanReview:
    payload = _json_object(REVIEW_FIELDS)
    raw_decision = payload.get("decision")
    try:
        decision = HumanDecision(raw_decision)
    except (ValueError, TypeError) as error:
        values = ", ".join(item.value for item in HumanDecision)
        raise ApiError(
            "validation_error", f"decision must be one of: {values}", 400
        ) from error
    comment = payload.get("comment", "")
    if not isinstance(comment, str):
        raise ApiError("validation_error", "comment must be a string", 400)
    comment = comment.strip()
    if len(comment) > MAX_COMMENT_CHARS:
        raise ApiError(
            "validation_error",
            f"comment must be at most {MAX_COMMENT_CHARS} characters",
            400,
        )
    if decision is HumanDecision.CORRECT and not comment:
        raise ApiError(
            "validation_error", "comment is required when decision is correct", 400
        )
    return HumanReview(decision=decision, comments=comment, timestamp=utc_now())


def create_app(
    orchestrator: MultiAgentOrchestrator,
    config: ServerConfig,
    workflow_executor: Optional[Any] = None,
) -> Flask:
    """Create an HTTP adapter over the shared persisted orchestrator."""

    app = Flask(__name__)
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    hosts = allowed_hosts(config)
    browser_origins = {
        f"http://{host}" for host in hosts if not host.startswith("host.docker.internal")
    }
    executor = workflow_executor or ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="homs-multi-agent"
    )
    app.extensions["multi_agent_workflow_executor"] = executor

    def process_workflow(workflow_id: str) -> None:
        try:
            orchestrator.process(workflow_id)
        except Exception:  # Background boundary must always reach a terminal state.
            def fail_if_still_running(record: Dict[str, Any]) -> Dict[str, Any]:
                if record.get("final_status") == "in_progress":
                    record["final_status"] = "failed"
                    record["final_result"] = {
                        "status": "failed",
                        "error": "workflow execution failed",
                        "mutation_executed": False,
                    }
                    record["completed_at"] = utc_now()
                return record

            try:
                orchestrator.audit_store.update_existing(
                    workflow_id, fail_if_still_running
                )
            except (RuntimeError, OSError):
                pass
            return

    for pending_workflow_id in orchestrator.audit_store.workflow_ids_with_status(
        "in_progress"
    ):
        executor.submit(process_workflow, pending_workflow_id)

    @app.before_request
    def enforce_local_access():
        if request.host not in hosts:
            return _error("host_not_allowed", "Host header is not allowed", 421)
        origin = request.headers.get("Origin")
        if origin and origin not in browser_origins:
            return _error("origin_not_allowed", "Browser origin is not allowed", 403)
        return None

    @app.errorhandler(ApiError)
    def api_error(error: ApiError):
        return _error(error.code, error.message, error.status)

    @app.errorhandler(413)
    def too_large(_error_value):
        return _error(
            "validation_error",
            f"Request body must be at most {MAX_BODY_BYTES} bytes",
            413,
        )

    @app.errorhandler(404)
    def unknown_endpoint(_error_value):
        return _error("not_found", "Unknown endpoint", 404)

    @app.errorhandler(405)
    def wrong_method(_error_value):
        return _error("method_not_allowed", "Method not allowed", 405)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "multi-agent-server"})

    @app.post("/workflows")
    def create_workflow():
        task, evidence = _workflow_request()
        try:
            record, _audit_path = orchestrator.create(task=task, evidence=evidence)
        except (RuntimeError, ValueError) as error:
            raise ApiError(
                "workflow_creation_failed",
                "Workflow could not be created",
                503,
            ) from error
        try:
            executor.submit(process_workflow, record["workflow_id"])
        except RuntimeError as error:
            record["final_status"] = "failed"
            record["final_result"] = {
                "status": "failed",
                "error": "workflow execution could not be scheduled",
                "mutation_executed": False,
            }
            record["completed_at"] = utc_now()
            orchestrator.audit_store.update(record)
            raise ApiError(
                "workflow_execution_unavailable",
                "Workflow execution is temporarily unavailable",
                503,
            ) from error
        location = f"/workflows/{record['workflow_id']}"
        return (
            jsonify(workflow_view(record)),
            202,
            {"Location": location, "Retry-After": "1"},
        )

    @app.get("/workflows")
    def list_workflows():
        evidence_type, limit, evidence_path, evidence_value = _history_query()
        records = orchestrator.audit_store.list_by_evidence_type(
            evidence_type,
            limit=limit,
            evidence_path=evidence_path,
            evidence_value=evidence_value,
        )
        return jsonify(
            {
                "schema_version": SCHEMA_VERSION,
                "count": len(records),
                "workflows": [workflow_summary(record) for record in records],
            }
        )

    @app.get("/workflows/<workflow_id>")
    def get_workflow(workflow_id: str):
        workflow_id = _validated_workflow_id(workflow_id)
        include_evidence = _include_evidence_requested()
        try:
            record = orchestrator.audit_store.read(workflow_id)
        except AuditNotFoundError as error:
            raise ApiError("not_found", "Workflow not found", 404) from error
        return jsonify(workflow_view(record, include_evidence=include_evidence))

    @app.post("/workflows/<workflow_id>/review")
    def submit_review(workflow_id: str):
        workflow_id = _validated_workflow_id(workflow_id)
        human_review = _human_review_request()
        try:
            record, _audit_path = orchestrator.submit_human_review(
                workflow_id=workflow_id, human_review=human_review
            )
        except AuditNotFoundError as error:
            raise ApiError("not_found", "Workflow not found", 404) from error
        except InvalidWorkflowStateError as error:
            raise ApiError("invalid_workflow_state", str(error), 409) from error
        return jsonify(workflow_view(record))

    return app


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the local HTTP interface for the shared Multi-Agent workflow."
    )
    add_model_arguments(parser)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    config = load_server_config()
    orchestrator = build_orchestrator(args, reports_dir=args.reports_dir)
    app = create_app(orchestrator, config)
    app.run(host=config.host, port=config.port, threaded=True)


if __name__ == "__main__":
    main()
