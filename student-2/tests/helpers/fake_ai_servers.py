"""
fake_ai_servers.py - tiny fake MCP, RAG and Multi-Agent servers for tests and CI.

Why: tests and GitHub Actions must never need the real shared MCP server, the
real RAG server, the real Multi-Agent Server or Ollama. These fakes run on localhost, on a random free port,
in a background thread, and answer with the same reply shapes the real servers
sent in the recent logged runs:

  * FakeMcpServer - speaks the MCP Streamable HTTP protocol (JSON replies), so
    the real `mcp` SDK client inside services/mcp_client.py can talk to it.
    Tools: homs_open_care_tasks (Student 2's) and homs_echo, using the shared
    {schema_version, ok, tool, data, error} envelope.
  * FakeRagServer - answers POST /query with the real grounded reply and the
    real insufficient-context reply (schema 1.0), and the real error body.
  * FakeMultiAgentServer - the workflow API of ai-services/multi-agent-server:
    create (202, in_progress), get, review, and the history search. A test moves
    a workflow on with advance() and decide(); two canned outcomes (ready / not
    ready) and the real final status for each decision.

All three record every HTTP request they receive, and can be switched between
response modes at any time with set_mode().

Standard library only (plus pytest for the fixtures at the bottom).
"""

import copy
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl
from uuid import UUID, uuid4

import pytest

# ------------------------------------------------------------
# Modes
# ------------------------------------------------------------
NORMAL = "normal"
INSUFFICIENT = "insufficient"    # RAG only
TOOL_ERROR = "tool_error"        # MCP only
BAD_REPLY = "bad_reply"
SLOW = "slow"
SERVER_ERROR = "server_error"

DEFAULT_SLOW_DELAY = 3.0  # seconds; keep your test's timeout below this

# ------------------------------------------------------------
# Real MCP reply pieces (ai-services/mcp-server)
# ------------------------------------------------------------
MCP_PATH = "/mcp"
MCP_SCHEMA_VERSION = "1.0"
CARE_TASKS_TOOL = "homs_open_care_tasks"
ECHO_TOOL = "homs_echo"
MCP_SERVER_INFO = {"name": "HOMS Shared MCP Server", "version": "0.1.0"}

# What tools/list returns (name, title, description, inputSchema as advertised).
MCP_TOOLS = [
    {
        "name": ECHO_TOOL,
        "title": "HOMS Echo",
        "description": (
            "Echo one bounded string to validate MCP connectivity and the shared "
            "structured result contract. Performs no external I/O."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "Text to return unchanged.",
                }
            },
            "required": ["message"],
            "additionalProperties": False,
        },
    },
    {
        "name": CARE_TASKS_TOOL,
        "title": "HOMS Open Care Tasks",
        "description": (
            "Read the pending and acknowledged nurse care tasks for one admission from "
            "Student 2's published backend API: task id, description, status and due "
            "time only. Read-only; does not create, acknowledge, complete or cancel tasks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "admission_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Required. Positive whole-number admission id.",
                }
            },
            "additionalProperties": False,
        },
    },
]

# Normal open-care-tasks data (same tasks as the MCP server's own test).
OPEN_TASKS = [
    {"task_id": 1, "description": "Check observations", "status": "pending", "due_at": None},
    {
        "task_id": 4,
        "description": "Change dressing",
        "status": "acknowledged",
        "due_at": "2026-09-30T10:00:00",
    },
]

# ------------------------------------------------------------
# Real RAG reply pieces (ai-services/rag-server)
# ------------------------------------------------------------
RAG_PATH = "/query"
RAG_SCHEMA_VERSION = "1.0"
RAG_FEATURES = ("student-1", "student-2", "student-3", "student-4", "student-5")
RAG_MAX_QUESTION_LENGTH = 500
RAG_MAX_TOP_K = 8
RAG_MODELS = {"embedding": "nomic-embed-text", "generation": "llama3.2:3b"}
RAG_INSUFFICIENT_MESSAGE = (
    "The knowledge base does not contain enough relevant information to answer "
    "this question, so no answer was generated."
)


def _json_bytes(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ------------------------------------------------------------
# Recording and the shared server plumbing
# ------------------------------------------------------------
@dataclass
class RecordedRequest:
    """One HTTP request as received. `body` is parsed JSON, or raw text if not JSON."""

    method: str
    path: str
    headers: dict
    body: object


class _Handler(BaseHTTPRequestHandler):
    """Hands every request to the owning fake; never logs to the terminal."""

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw
        record = RecordedRequest(
            self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body
        )
        status, content_type, payload = self.server.owner._handle(record)
        try:
            self.send_response(status)
            if payload:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            pass  # the client gave up (e.g. its timeout fired) - nothing to do

    do_GET = do_POST = do_DELETE = do_PUT = _serve

    def log_message(self, *args):
        pass


class _FakeServer:
    """Start/stop, mode switching and request recording shared by both fakes."""

    MODES = (NORMAL,)
    URL_PATH = ""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests = []
        self._mode = (NORMAL, {})
        self._stopping = threading.Event()  # also wakes any "slow" sleepers
        self._httpd = None
        self._thread = None

    # -- lifecycle -------------------------------------------------------- #
    def start(self):
        """Bind 127.0.0.1 on a free port (port 0 = the OS picks) and serve."""
        if self._httpd is None:
            self._stopping.clear()
            self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            self._httpd.daemon_threads = True  # a stuck handler can't hang exit
            self._httpd.owner = self
            # Short poll interval so stop() returns quickly.
            self._thread = threading.Thread(
                target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
            )
            self._thread.start()
        return self

    def stop(self):
        """Stop serving and free the port. Safe to call twice."""
        if self._httpd is None:
            return
        self._stopping.set()
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        self._httpd = self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()

    @property
    def port(self):
        return self._httpd.server_address[1]

    @property
    def url(self):
        """The value to use for MCP_SERVER_URL / RAG_SERVER_URL."""
        return f"http://127.0.0.1:{self.port}{self.URL_PATH}"

    # -- modes ------------------------------------------------------------ #
    def set_mode(self, mode, **options):
        """
        Choose how the next requests are answered (until changed again).

        Options: slow -> delay=seconds; server_error -> status=HTTP code;
        bad_reply -> variant=...; tool_error (MCP) -> code=, message=, details=.
        """
        if mode not in self.MODES:
            raise ValueError(f"{type(self).__name__} has no mode '{mode}'; use one of {self.MODES}")
        with self._lock:
            self._mode = (mode, options)

    # -- recording -------------------------------------------------------- #
    @property
    def requests(self):
        """Every HTTP request received so far, oldest first (a copy)."""
        with self._lock:
            return list(self._requests)

    @property
    def request_count(self):
        return len(self.requests)

    def reset(self):
        """Forget recorded requests and go back to normal mode."""
        with self._lock:
            self._requests.clear()
            self._mode = (NORMAL, {})

    # -- request handling ------------------------------------------------- #
    def _handle(self, record):
        with self._lock:
            self._requests.append(record)  # recorded before any delay or failure
            mode, options = self._mode
        if mode == SLOW:
            # Interruptible sleep, then answer normally.
            self._stopping.wait(options.get("delay", DEFAULT_SLOW_DELAY))
            mode, options = NORMAL, {}
        elif mode == SERVER_ERROR:
            return self._server_error(options.get("status", 503))
        return self._respond(record, mode, options)

    def _server_error(self, status):
        raise NotImplementedError

    def _respond(self, record, mode, options):
        raise NotImplementedError


# ------------------------------------------------------------
# Fake MCP server
# ------------------------------------------------------------
def _envelope_ok(tool, data):
    return {"schema_version": MCP_SCHEMA_VERSION, "ok": True, "tool": tool, "data": data, "error": None}


def _envelope_error(tool, code, message, details=None):
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "ok": False,
        "tool": tool,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def _care_tasks_envelope(arguments):
    """Normal care-tasks reply, including the real input-validation errors."""
    admission_id = arguments.get("admission_id")
    if admission_id is None:
        return _envelope_error(
            CARE_TASKS_TOOL, "invalid_input", "'admission_id' is required.",
            {"field": "admission_id", "reason": "missing"},
        )
    if type(admission_id) is not int:
        return _envelope_error(
            CARE_TASKS_TOOL, "invalid_input", "'admission_id' must be a whole number.",
            {"field": "admission_id", "reason": "wrong_type",
             "received_type": type(admission_id).__name__},
        )
    if admission_id < 1:
        return _envelope_error(
            CARE_TASKS_TOOL, "invalid_input", "'admission_id' must be a positive whole number.",
            {"field": "admission_id", "reason": "not_positive"},
        )
    return _envelope_ok(
        CARE_TASKS_TOOL,
        {"admission_id": admission_id, "count": len(OPEN_TASKS), "tasks": OPEN_TASKS},
    )


def _echo_envelope(arguments):
    if "message" not in arguments:
        # The real server reports validation errors under the homs_echo name.
        return _envelope_error(
            ECHO_TOOL, "validation_error", "'message' is required.",
            {"field": "message", "reason": "required"},
        )
    return _envelope_ok(ECHO_TOOL, {"message": arguments["message"]})


def _tool_result(envelope, structured=True):
    """Wrap an envelope as an MCP tools/call result (text + structured + isError)."""
    result = {
        "content": [{"type": "text", "text": _json_bytes(envelope).decode("utf-8")}],
        "isError": not envelope["ok"],
    }
    if structured:
        result["structuredContent"] = envelope
    return result


class FakeMcpServer(_FakeServer):
    """
    Modes: normal, tool_error (code=..., message=..., details=...),
    bad_reply (variant="missing_fields" | "no_structured"), slow, server_error.
    """

    MODES = (NORMAL, TOOL_ERROR, BAD_REPLY, SLOW, SERVER_ERROR)
    URL_PATH = MCP_PATH

    @property
    def tool_calls(self):
        """Each tools/call received, as {"name": ..., "arguments": ...}."""
        calls = []
        for record in self.requests:
            body = record.body
            if record.method == "POST" and isinstance(body, dict) and body.get("method") == "tools/call":
                params = body.get("params") or {}
                calls.append({"name": params.get("name"), "arguments": params.get("arguments") or {}})
        return calls

    @property
    def tool_call_count(self):
        return len(self.tool_calls)

    def _server_error(self, status):
        return status, "text/plain", b"Internal Server Error"

    def _rpc_result(self, message, result):
        return 200, "application/json", _json_bytes(
            {"jsonrpc": "2.0", "id": message["id"], "result": result}
        )

    def _rpc_error(self, message_id, code, text, status=200):
        return status, "application/json", _json_bytes(
            {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}
        )

    def _respond(self, record, mode, options):
        if record.path.split("?")[0] != MCP_PATH:
            return 404, "text/plain", b"Not Found"
        if record.method != "POST":
            # No optional SSE stream and no session to close (both allowed).
            return 405, "text/plain", b"Method Not Allowed"

        message = record.body
        if not isinstance(message, dict) or "method" not in message:
            return self._rpc_error(None, -32700, "Parse error", status=400)
        if "id" not in message:
            return 202, "text/plain", b""  # notification, e.g. initialized

        method = message["method"]
        params = message.get("params") or {}
        if method == "initialize":
            return self._rpc_result(message, {
                "protocolVersion": params.get("protocolVersion"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": MCP_SERVER_INFO,
            })
        if method == "ping":
            return self._rpc_result(message, {})
        if method == "tools/list":
            return self._rpc_result(message, {"tools": MCP_TOOLS})
        if method == "tools/call":
            return self._rpc_result(message, self._call_tool(params, mode, options))
        return self._rpc_error(message["id"], -32601, "Method not found")

    def _call_tool(self, params, mode, options):
        name = params.get("name")
        arguments = params.get("arguments") or {}

        if mode == TOOL_ERROR:
            code = options.get("code", "upstream_unavailable")
            if code == "upstream_unavailable" and "message" not in options:
                # The real upstream failure, as the care-tasks tool reports it.
                message, details = "Student 2 clinical API is unavailable.", {"reason": "unreachable"}
            else:
                message, details = "The tool reported an error.", {}
            return _tool_result(_envelope_error(
                name, code, options.get("message", message), options.get("details", details)
            ))

        if name == CARE_TASKS_TOOL:
            envelope = _care_tasks_envelope(arguments)
        elif name == ECHO_TOOL:
            envelope = _echo_envelope(arguments)
        else:
            # The real server answers unknown tools with an echo-named error.
            envelope = _envelope_error(
                ECHO_TOOL, "validation_error", f"Unknown tool: {name}",
                {"reason": "unknown_tool", "received": name},
            )

        if mode == BAD_REPLY:
            variant = options.get("variant", "missing_fields")
            if variant == "no_structured":
                return _tool_result(envelope, structured=False)
            if variant != "missing_fields":
                raise ValueError(f"unknown bad_reply variant '{variant}'")
            # Envelope without its "data" part.
            broken = {k: v for k, v in envelope.items() if k != "data"}
            return {"content": _tool_result(envelope)["content"],
                    "structuredContent": broken, "isError": False}
        return _tool_result(envelope)


# ------------------------------------------------------------
# Fake RAG server
# ------------------------------------------------------------
def _rag_error_body(code, message):
    return {"schema_version": RAG_SCHEMA_VERSION,
            "error": {"code": code, "message": message, "details": {}}}


def _rag_validate(body):
    """The real server's request checks; returns an error body or None."""
    if not isinstance(body, dict):
        return _rag_error_body("validation_error", "Request body must be a JSON object")
    unexpected = sorted(set(body) - {"question", "feature", "top_k"})
    if unexpected:
        return _rag_error_body("validation_error", f"Unexpected field(s): {', '.join(unexpected)}")
    question, feature, top_k = body.get("question"), body.get("feature"), body.get("top_k")
    if not isinstance(question, str) or not question.strip():
        return _rag_error_body("validation_error", "'question' must be a non-blank string")
    if len(question) > RAG_MAX_QUESTION_LENGTH:
        return _rag_error_body(
            "validation_error", f"'question' must be at most {RAG_MAX_QUESTION_LENGTH} characters")
    if feature is not None and feature not in RAG_FEATURES:
        return _rag_error_body(
            "validation_error", f"'feature' must be one of: {', '.join(RAG_FEATURES)}")
    if top_k is not None and (type(top_k) is not int or not 1 <= top_k <= RAG_MAX_TOP_K):
        return _rag_error_body(
            "validation_error", f"'top_k' must be an integer between 1 and {RAG_MAX_TOP_K}")
    return None


def _rag_reply(status, question, feature, answer, confidence, citations, reason, retrieval, duration_ms):
    return {
        "schema_version": RAG_SCHEMA_VERSION,
        "status": status,
        "question": question,
        "feature": feature,
        "answer": answer,
        "confidence": confidence,
        "citations": citations,
        "reason": reason,
        "retrieval": retrieval,
        "models": dict(RAG_MODELS),
        "duration_ms": duration_ms,
    }


def grounded_reply(question, feature):
    """A real 'answered' reply (care-task escalation question)."""
    return _rag_reply(
        "answered", question, feature,
        "According to [S1], a pending nurse care task that has not been acknowledged "
        "within 60 minutes is considered overdue. You should tell the nurse in charge "
        "about the overdue care task. Additionally, if the delay could affect the "
        "patient's plan, you should also inform the assigned doctor.",
        "medium",
        [{
            "id": "S1",
            "source": "student-2/care-task-escalation.md",
            "title": "Care Task Escalation",
            "section": "When a pending care task is overdue",
            "score": 0.7034,
            "snippet": (
                "A pending nurse care task that has not been acknowledged within 60 "
                "minutes is considered overdue. The nurse who notices an overdue care "
                "task should tell the nurse in charge. If the delay could affect the "
                "patient's plan, the nurse should also tell the assigned doctor."
            ),
        }],
        None,
        {"top_score": 0.7034, "threshold": 0.62, "considered": 4, "relevant": 3},
        5128,
    )


def insufficient_reply(question, feature):
    """A real 'insufficient_context' reply (nothing relevant found)."""
    return _rag_reply(
        "insufficient_context", question, feature, RAG_INSUFFICIENT_MESSAGE, "none", [],
        "no_relevant_context",
        {"top_score": 0.5014, "threshold": 0.62, "considered": 4, "relevant": 0},
        195,
    )


class FakeRagServer(_FakeServer):
    """
    Modes: normal, insufficient, bad_reply (variant="missing_fields" | "not_json"),
    slow, server_error (status=503 default; 504 gives the Ollama-timeout body).
    """

    MODES = (NORMAL, INSUFFICIENT, BAD_REPLY, SLOW, SERVER_ERROR)

    @property
    def queries(self):
        """The JSON body of each POST /query received."""
        return [r.body for r in self.requests if r.method == "POST" and r.path == RAG_PATH]

    @property
    def query_count(self):
        return len(self.queries)

    def _server_error(self, status):
        # The real server's error bodies when Ollama is down / too slow.
        if status == 504:
            body = _rag_error_body("ollama_timeout", "Ollama did not respond in time")
        else:
            body = _rag_error_body("ollama_unavailable", "Ollama is not reachable")
        return status, "application/json", _json_bytes(body)

    def _respond(self, record, mode, options):
        if record.path != RAG_PATH:
            return 404, "application/json", _json_bytes(_rag_error_body("not_found", "Unknown endpoint"))
        if record.method != "POST":
            return 405, "application/json", _json_bytes(
                _rag_error_body("method_not_allowed", "Method not allowed for this endpoint"))

        problem = _rag_validate(record.body)
        if problem:
            return 400, "application/json", _json_bytes(problem)

        question, feature = record.body["question"].strip(), record.body.get("feature")
        if mode == BAD_REPLY:
            variant = options.get("variant", "missing_fields")
            if variant == "not_json":
                return 200, "text/html", b"<html>not json</html>"
            if variant != "missing_fields":
                raise ValueError(f"unknown bad_reply variant '{variant}'")
            # 200 with the essential answer parts left out.
            return 200, "application/json", _json_bytes({
                "schema_version": RAG_SCHEMA_VERSION, "status": "answered",
                "question": question, "feature": feature,
            })
        reply = insufficient_reply if mode == INSUFFICIENT else grounded_reply
        return 200, "application/json", _json_bytes(reply(question, feature))


# ------------------------------------------------------------
# Fake Multi-Agent Server
# ------------------------------------------------------------
# Real pieces from ai-services/multi-agent-server (api.py, orchestrator.py,
# audit.py) and the finished runs in reports/multi-agent/.
MA_SCHEMA_VERSION = "1.0"
MA_WORKFLOWS_PATH = "/workflows"
MA_HEALTH_PATH = "/health"
MA_RETRY_AFTER = "1"
MA_MAX_TASK_CHARS = 2_000
MA_MAX_COMMENT_CHARS = 10_000
MA_MAX_EVIDENCE_TYPE_CHARS = 120
MA_MAX_HISTORY_RESULTS = 100
MA_MAX_EVIDENCE_PATH_CHARS = 160

# Extra modes (normal, server_error, bad_reply and slow are shared, above).
FAILED_WORKFLOW = "failed_workflow"  # advance() ends the workflow as failed
WRONG_PREFIX = "wrong_prefix"        # workflows are created with a foreign task

# Workflow statuses (final_status in the audit record).
IN_PROGRESS = "in_progress"
AWAITING_HUMAN_REVIEW = "awaiting_human_review"
FAILED = "failed"

# Canned outcomes for advance().
READY = "ready"          # all three checks passed: Worker proposal_ready
NOT_READY = "not_ready"  # Worker no_eligible_option, failed checks shown

# What each decision finishes the workflow as (orchestrator.submit_human_review).
MA_DECISION_STATUS = {
    "approve": "approved",
    "correct": "corrected",
    "reject": "rejected",
    "partially_accept": "partially_accepted",
}

# The default live role mapping (README) and what the logged runs recorded.
MA_MODELS = {"planner": "llama3.1:8b", "worker": "llama3.2:3b", "reviewer": "deepseek-r1:8b"}
MA_FAILURE_MESSAGE = "reviewer output remained invalid after 1 attempts: model response was empty"

# Student 2's discharge review (backend/services/discharge_evidence.py). Kept as
# plain values here so the fake needs nothing from the backend.
DISCHARGE_TASK_PREFIX = "Discharge readiness review: "
DISCHARGE_OPTION_ID = "ready_to_discharge"
DISCHARGE_METRIC = "readiness checks met"
DISCHARGE_CHECKS = ("no_open_care_tasks", "no_pending_consultations", "no_scheduled_surgery")
WRONG_PREFIX_TASK = "Review the selected hospital shift for staffing coverage problems."

_PLANNED_STEPS = (
    "Review the supplied open care task, pending consultation and scheduled surgery counts.",
    "Evaluate the ready_to_discharge option against every hard constraint.",
    "Compare the readiness checks met with the target.",
    "Document the unknowns and limitations of the evidence.",
)
_REQUIRED_EVIDENCE = (
    "current_state", "open_care_tasks", "pending_consultations", "scheduled_surgeries",
    "objective", "options", "constraints", "unknowns",
)


class _ApiError(Exception):
    """Mirrors api.ApiError: a code, a message and an HTTP status."""

    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class _StateError(ValueError):
    """The workflow is not in the state the action needs (the real server's 409)."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ma_error_body(code, message):
    return {"schema_version": MA_SCHEMA_VERSION, "error": {"code": code, "message": message}}


def _execution(role, error=None):
    """The public execution view of one stage (api._execution_view fields only)."""
    execution = {
        "provider": "live_ollama",
        "model": MA_MODELS[role],
        "fallback_used": False,
        "fallback_reason": None,
        "attempt_count": 1,
    }
    if error is not None:
        execution["error"] = error
    return execution


def _texts(evidence, key, field=None):
    """Strings out of the submitted evidence (unknowns / constraint descriptions), else []."""
    items = evidence.get(key) if isinstance(evidence, dict) else None
    if not isinstance(items, list):
        return []
    if field is None:
        return [item for item in items if isinstance(item, str)]
    return [item[field] for item in items if isinstance(item, dict) and isinstance(item.get(field), str)]


def _planner_output(evidence):
    return {
        "goal": "Judge from the supplied checks whether the admission looks ready for discharge.",
        "required_evidence": list(_REQUIRED_EVIDENCE),
        "constraints": _texts(evidence, "constraints", "description"),
        "planned_steps": list(_PLANNED_STEPS),
        "expected_result": (
            "A readiness recommendation based only on the supplied evidence, including "
            "limitations and missing information."
        ),
    }


def _worker_output(outcome, failed_checks, evidence):
    met = len(DISCHARGE_CHECKS) - (0 if outcome == READY else len(failed_checks))
    checks = [
        {"constraint_id": check, "passed": outcome == READY or check not in failed_checks}
        for check in DISCHARGE_CHECKS
    ]
    output = {
        "status": "proposal_ready" if outcome == READY else "no_eligible_option",
        "plan_steps_followed": list(_PLANNED_STEPS),
        "missing_planner_evidence": [],
        "gap": {
            "metric": DISCHARGE_METRIC,
            "current": met,
            "target": len(DISCHARGE_CHECKS),
            "deficit": len(DISCHARGE_CHECKS) - met,
        },
        "evaluated_options": [{
            "option_id": DISCHARGE_OPTION_ID,
            "label": DISCHARGE_OPTION_ID,
            "hard_constraint_checks": checks,
            "eligible": outcome == READY,
            "risk_indicators": [],
        }],
    }
    if outcome == READY:
        output["proposed_solution"] = {
            "selected_option_id": DISCHARGE_OPTION_ID,
            "selected_option": DISCHARGE_OPTION_ID,
            "metric_delta": 0,
            "projected_value": len(DISCHARGE_CHECKS),
            "reasoning": ["Passed hard constraint: {}".format(check) for check in DISCHARGE_CHECKS],
        }
    # proposed_solution for no_eligible_option: not shown (the logged runs only
    # hold messy live-model output for that status), so it is left out.
    output["limitations"] = _texts(evidence, "unknowns")
    output["mutation_executed"] = False
    return output


def _reviewer_output(outcome, worker_output):
    ready = outcome == READY
    output = {
        "review_status": "validated_with_risks" if ready else "changes_required",
        "validated_findings": [],
    }
    if ready:
        output["validated_evidence_refs"] = [{
            "source": "worker_output",
            "path": "proposed_solution.projected_value",
            "value": len(DISCHARGE_CHECKS),
        }]
    output.update({
        "risks": list(worker_output["limitations"]),
        "unsupported_claims": [],
        # null when the Worker found no eligible option (README: string or null).
        "recommended_option_id": DISCHARGE_OPTION_ID if ready else None,
        "recommendation": "Review the validated Worker proposal and authoritative evidence before action.",
        "human_approval_required": True,
        "mutation_verified_absent": True,
    })
    return output


def _stage(output, execution):
    now = _utc_now()
    return {"started_at": now, "completed_at": now, "output": output, "execution": execution}


def _stage_view(stage):
    if not isinstance(stage, dict):
        return None
    return {
        "started_at": stage.get("started_at"),
        "completed_at": stage.get("completed_at"),
        "output": stage.get("output"),
        "execution": stage.get("execution"),
    }


def _workflow_view(record, include_evidence=False):
    """api.workflow_view: the public view of one audit record."""
    view = {
        "schema_version": record["schema_version"],
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


def _workflow_summary(record):
    """api.workflow_summary: only what is needed to find and reopen a run."""
    return {
        "workflow_id": record["workflow_id"],
        "status": record["final_status"],
        "started_at": record.get("timestamp"),
        "human_review": record.get("human_review"),
    }


def _json_object(record, allowed_fields):
    """api._json_object. Like Flask's get_json(silent=True), it needs a JSON Content-Type."""
    content_type = (record.headers.get("content-type") or "").split(";")[0].strip().lower()
    is_json = content_type == "application/json" or content_type.endswith("+json")
    payload = record.body if is_json else None
    if not isinstance(payload, dict):
        raise _ApiError("validation_error", "Request body must be a JSON object", 400)
    unexpected = sorted(set(payload) - allowed_fields)
    if unexpected:
        raise _ApiError("validation_error", "Unexpected field(s): {}".format(", ".join(unexpected)), 400)
    return payload


def _query_args(query, allowed):
    """api._history_query / _include_evidence_requested: first value wins, extras rejected."""
    args = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        args.setdefault(key, value)
    unexpected = sorted(set(args) - set(allowed))
    if unexpected:
        raise _ApiError(
            "validation_error", "Unexpected query field(s): {}".format(", ".join(unexpected)), 400)
    return args


def _validated_workflow_id(value):
    """api._validated_workflow_id: anything that is not a canonical UUID is a 404."""
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise _ApiError("not_found", "Workflow not found", 404) from None
    if str(parsed) != value.lower():
        raise _ApiError("not_found", "Workflow not found", 404)
    return str(parsed)


class _HeaderHandler(_Handler):
    """
    _Handler that also sends response headers: a fake's reply may carry a 4th
    item, a dict (Location / Retry-After). Replies with three items work as before.
    """

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = raw
        record = RecordedRequest(
            self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body
        )
        status, content_type, payload, *rest = self.server.owner._handle(record)
        try:
            self.send_response(status)
            if payload:
                self.send_header("Content-Type", content_type)
            for name, value in (rest[0] if rest else {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            pass  # the client gave up (e.g. its timeout fired) - nothing to do

    do_GET = do_POST = do_DELETE = do_PUT = _serve


class FakeMultiAgentServer(_FakeServer):
    """
    The Multi-Agent Server workflow API, kept in memory.

    Workflows start in_progress, as POST /workflows leaves them. Nothing moves
    on its own: a test calls advance() (the agents finish) and then decides
    through POST .../review or decide().

    Modes: normal, server_error (status=503 default), bad_reply
    (variant="missing_fields" | "not_json"), slow (delay=seconds),
    failed_workflow (advance() ends the workflow failed instead of awaiting
    review) and wrong_prefix (POST /workflows stores a task that does not start
    with the discharge prefix, whatever was sent). The mode is read when the
    request arrives (or, for failed_workflow, when advance() is called).
    """

    MODES = (NORMAL, SERVER_ERROR, BAD_REPLY, SLOW, FAILED_WORKFLOW, WRONG_PREFIX)
    URL_PATH = ""

    def __init__(self):
        super().__init__()
        self._workflows = {}  # workflow_id -> audit record, oldest first

    def start(self):
        super().start()
        # Swap in the handler that can send headers; no request has arrived yet.
        self._httpd.RequestHandlerClass = _HeaderHandler
        return self

    def reset(self):
        """Forget recorded requests and workflows, and go back to normal mode."""
        super().reset()
        with self._lock:
            self._workflows.clear()

    # -- what a test can ask about ---------------------------------------- #
    @property
    def submissions(self):
        """The JSON body of each POST /workflows received, oldest first."""
        return [r.body for r in self.requests if r.method == "POST" and self._path(r) == MA_WORKFLOWS_PATH]

    @property
    def submission_count(self):
        return len(self.submissions)

    @property
    def reviews(self):
        """Each review POST received, as {"workflow_id": ..., "body": ...}."""
        found = []
        for r in self.requests:
            parts = self._path(r).split("/")
            if r.method == "POST" and len(parts) == 4 and parts[1] == "workflows" and parts[3] == "review":
                found.append({"workflow_id": parts[2], "body": r.body})
        return found

    @property
    def history_queries(self):
        """The query of each GET /workflows search received, as a dict."""
        return [dict(parse_qsl(r.path.partition("?")[2], keep_blank_values=True))
                for r in self.requests if r.method == "GET" and self._path(r) == MA_WORKFLOWS_PATH]

    @property
    def workflow_ids(self):
        """Every workflow id, oldest first."""
        with self._lock:
            return list(self._workflows)

    @property
    def latest_workflow_id(self):
        ids = self.workflow_ids
        return ids[-1] if ids else None

    def workflow(self, workflow_id=None):
        """A copy of the stored audit record (default: the latest workflow)."""
        with self._lock:
            return copy.deepcopy(self._get(workflow_id))

    # -- moving workflows along, without HTTP ----------------------------- #
    def create_workflow(self, task, evidence, started_at=None):
        """Add an in_progress workflow directly (not recorded as a request). Returns its id."""
        with self._lock:
            return self._create(task, evidence, started_at)

    def advance(self, workflow_id=None, outcome=None, failed_checks=("no_open_care_tasks",)):
        """
        The agents finish: in_progress -> awaiting_human_review (default: the
        latest workflow). outcome READY has all three checks passed; NOT_READY
        has the Worker report no_eligible_option with `failed_checks` shown as
        failed. In failed_workflow mode the default outcome is FAILED: the
        reviewer stage fails and the workflow ends as `failed`.
        """
        with self._lock:
            if outcome is None:
                outcome = FAILED if self._mode[0] == FAILED_WORKFLOW else READY
            if outcome not in (READY, NOT_READY, FAILED):
                raise ValueError("outcome must be READY, NOT_READY or FAILED")
            failed_checks = tuple(failed_checks)
            if set(failed_checks) - set(DISCHARGE_CHECKS) or (outcome == NOT_READY and not failed_checks):
                raise ValueError("failed_checks must name at least one of {}".format(DISCHARGE_CHECKS))
            record = self._get(workflow_id)
            if record["final_status"] != IN_PROGRESS:
                raise _StateError("workflow is not in progress")

            evidence = record["input_evidence"]
            planner = _planner_output(evidence)
            worker = _worker_output(READY if outcome == FAILED else outcome, failed_checks, evidence)
            record["planner"] = _stage(planner, _execution("planner"))
            record["worker"] = _stage(worker, _execution("worker"))
            if outcome == FAILED:
                record["reviewer"] = _stage(None, _execution("reviewer", error=MA_FAILURE_MESSAGE))
                record["final_status"] = FAILED
                record["final_result"] = {
                    "status": FAILED, "error": MA_FAILURE_MESSAGE, "mutation_executed": False}
            else:
                record["reviewer"] = _stage(_reviewer_output(outcome, worker), _execution("reviewer"))
                record["final_status"] = AWAITING_HUMAN_REVIEW
            return record["workflow_id"]

    def decide(self, workflow_id=None, decision="approve", comment=""):
        """Record a human decision directly (what POST .../review does, but not recorded)."""
        with self._lock:
            return _workflow_view(self._decide(self._get(workflow_id), decision, comment.strip()))

    # -- store (callers hold self._lock) ---------------------------------- #
    @staticmethod
    def _path(record):
        return record.path.partition("?")[0]

    def _get(self, workflow_id):
        if workflow_id is None:
            if not self._workflows:
                raise KeyError("no workflows yet")
            workflow_id = next(reversed(self._workflows))
        try:
            return self._workflows[workflow_id]
        except KeyError:
            raise KeyError("no workflow {}".format(workflow_id)) from None

    def _create(self, task, evidence, started_at=None):
        workflow_id = str(uuid4())
        self._workflows[workflow_id] = {
            "schema_version": MA_SCHEMA_VERSION,
            "workflow_id": workflow_id,
            "timestamp": started_at or _utc_now(),
            "task": task.strip(),
            "input_evidence": copy.deepcopy(evidence),
            "planner": None,
            "worker": None,
            "reviewer": None,
            "human_review": None,
            "final_status": IN_PROGRESS,
            "final_result": None,
        }
        return workflow_id

    @staticmethod
    def _decide(record, decision, comment):
        """orchestrator.submit_human_review: only an awaiting workflow can be decided."""
        if record["final_status"] != AWAITING_HUMAN_REVIEW:
            raise _StateError("workflow is not awaiting Human Review")
        record["human_review"] = {"decision": decision, "comments": comment, "timestamp": _utc_now()}
        record["final_status"] = MA_DECISION_STATUS[decision]
        record["final_result"] = {
            "status": record["final_status"],
            "proposal": record["worker"]["output"].get("proposed_solution"),
            "review": record["reviewer"]["output"],
            "human_decision": decision,
            "human_comments_or_correction": comment,
            "mutation_executed": False,
        }
        return record

    # -- request handling ------------------------------------------------- #
    def _server_error(self, status):
        if status == 503:  # the real 503 body (api.create_workflow)
            body = _ma_error_body(
                "workflow_execution_unavailable", "Workflow execution is temporarily unavailable")
            return status, "application/json", _json_bytes(body), {}
        return status, "text/plain", b"Internal Server Error", {}

    def _respond(self, record, mode, options):
        try:
            status, body, headers = self._route(record, mode)
        except _ApiError as error:
            return (error.status, "application/json",
                    _json_bytes(_ma_error_body(error.code, error.message)), {})
        if mode == BAD_REPLY and 200 <= status < 300:
            return self._bad_reply(status, body, headers, options)
        return status, "application/json", _json_bytes(body), headers

    @staticmethod
    def _bad_reply(status, body, headers, options):
        variant = options.get("variant", "missing_fields")
        if variant == "not_json":
            return status, "text/html", b"<html>not json</html>", headers
        if variant != "missing_fields":
            raise ValueError(f"unknown bad_reply variant '{variant}'")
        # A well-formed reply with its essential parts left out.
        broken = {k: v for k, v in body.items() if k not in ("workflow_id", "status", "workflows", "count")}
        return status, "application/json", _json_bytes(broken), headers

    def _route(self, record, mode):
        path, _, query = record.path.partition("?")
        parts = path.split("/")[1:] if path.startswith("/") else []
        method = record.method

        if parts == ["health"]:
            self._allow(method, "GET")
            return 200, {"status": "ok", "service": "multi-agent-server"}, {}
        if parts == ["workflows"]:
            if method == "POST":
                return self._create_route(record, mode)
            self._allow(method, "GET")
            return self._list_route(query)
        if len(parts) == 2 and parts[0] == "workflows" and parts[1]:
            self._allow(method, "GET")
            return self._get_route(parts[1], query)
        if len(parts) == 3 and parts[0] == "workflows" and parts[1] and parts[2] == "review":
            self._allow(method, "POST")
            return self._review_route(record, parts[1])
        raise _ApiError("not_found", "Unknown endpoint", 404)

    @staticmethod
    def _allow(method, allowed):
        if method != allowed:
            raise _ApiError("method_not_allowed", "Method not allowed", 405)

    def _create_route(self, record, mode):
        payload = _json_object(record, {"task", "evidence"})
        task, evidence = payload.get("task"), payload.get("evidence")
        if not isinstance(task, str) or not task.strip():
            raise _ApiError("validation_error", "task must be a non-blank string", 400)
        if len(task) > MA_MAX_TASK_CHARS:
            raise _ApiError(
                "validation_error", f"task must be at most {MA_MAX_TASK_CHARS} characters", 400)
        if not isinstance(evidence, dict) or not evidence:
            raise _ApiError("validation_error", "evidence must be a non-empty JSON object", 400)
        if mode == WRONG_PREFIX:
            task = WRONG_PREFIX_TASK
        with self._lock:
            workflow_id = self._create(task, evidence)
            view = _workflow_view(self._workflows[workflow_id])
        headers = {"Location": f"{MA_WORKFLOWS_PATH}/{workflow_id}", "Retry-After": MA_RETRY_AFTER}
        return 202, view, headers

    def _list_route(self, query):
        args = _query_args(query, ("evidence_type", "limit", "evidence_path", "evidence_value"))
        evidence_type = args.get("evidence_type", "").strip()
        if not evidence_type or len(evidence_type) > MA_MAX_EVIDENCE_TYPE_CHARS:
            raise _ApiError(
                "validation_error", f"evidence_type must be 1-{MA_MAX_EVIDENCE_TYPE_CHARS} characters", 400)
        try:
            limit = int(args.get("limit", "10"))
        except ValueError:
            raise _ApiError("validation_error", "limit must be an integer", 400) from None
        if not 1 <= limit <= MA_MAX_HISTORY_RESULTS:
            raise _ApiError(
                "validation_error", f"limit must be between 1 and {MA_MAX_HISTORY_RESULTS}", 400)
        evidence_path = args.get("evidence_path")
        evidence_value = args.get("evidence_value")
        if (evidence_path is None) != (evidence_value is None):
            raise _ApiError(
                "validation_error", "evidence_path and evidence_value must be supplied together", 400)
        if evidence_path is not None:
            evidence_path = evidence_path.strip()
            if (not evidence_path
                    or len(evidence_path) > MA_MAX_EVIDENCE_PATH_CHARS
                    or re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*", evidence_path) is None
                    or len(evidence_value) > MA_MAX_EVIDENCE_TYPE_CHARS):
                raise _ApiError("validation_error", "evidence filter is invalid", 400)

        with self._lock:
            matches = [
                (index, rec) for index, rec in enumerate(self._workflows.values())
                if self._matches(rec, evidence_type, evidence_path, evidence_value)
            ]
        # Newest first by start time; creation order breaks ties.
        matches.sort(key=lambda pair: (pair[1]["timestamp"], pair[0]), reverse=True)
        records = [rec for _index, rec in matches[:limit]]
        return 200, {
            "schema_version": MA_SCHEMA_VERSION,
            "count": len(records),
            "workflows": [_workflow_summary(rec) for rec in records],
        }, {}

    @staticmethod
    def _matches(record, evidence_type, evidence_path, evidence_value):
        """audit.list_by_evidence_type: type match, then a dotted-path scalar compared as text."""
        evidence = record.get("input_evidence")
        if not isinstance(evidence, dict) or evidence.get("evidence_type") != evidence_type:
            return False
        if evidence_path is None:
            return True
        current = evidence
        for key in evidence_path.split("."):
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        return not isinstance(current, (dict, list)) and str(current) == evidence_value

    def _get_route(self, raw_id, query):
        workflow_id = _validated_workflow_id(raw_id)
        args = _query_args(query, ("include_evidence",))
        value = args.get("include_evidence", "false").strip().lower()
        if value not in ("true", "false"):
            raise _ApiError("validation_error", "include_evidence must be 'true' or 'false'", 400)
        with self._lock:
            stored = self._workflows.get(workflow_id)
            if stored is None:
                raise _ApiError("not_found", "Workflow not found", 404)
            return 200, _workflow_view(stored, include_evidence=value == "true"), {}

    def _review_route(self, record, raw_id):
        workflow_id = _validated_workflow_id(raw_id)
        payload = _json_object(record, {"decision", "comment"})
        decision = payload.get("decision")
        if not isinstance(decision, str) or decision not in MA_DECISION_STATUS:
            raise _ApiError(
                "validation_error", "decision must be one of: {}".format(", ".join(MA_DECISION_STATUS)), 400)
        comment = payload.get("comment", "")
        if not isinstance(comment, str):
            raise _ApiError("validation_error", "comment must be a string", 400)
        comment = comment.strip()
        if len(comment) > MA_MAX_COMMENT_CHARS:
            raise _ApiError(
                "validation_error", f"comment must be at most {MA_MAX_COMMENT_CHARS} characters", 400)
        if decision == "correct" and not comment:
            raise _ApiError("validation_error", "comment is required when decision is correct", 400)

        with self._lock:
            stored = self._workflows.get(workflow_id)
            if stored is None:
                raise _ApiError("not_found", "Workflow not found", 404)
            try:
                self._decide(stored, decision, comment)
            except _StateError as error:
                raise _ApiError("invalid_workflow_state", str(error), 409) from None
            return 200, _workflow_view(stored), {}


# ------------------------------------------------------------
# pytest fixtures
# ------------------------------------------------------------
# Each fixture starts a fresh fake, points the settings environment at it (with
# the feature switched ON) and stops it afterwards. monkeypatch undoes the env
# changes, so tests can still override e.g. MCP_ENABLED=false themselves.
@pytest.fixture
def fake_mcp_server(monkeypatch):
    with FakeMcpServer() as server:
        monkeypatch.setenv("MCP_ENABLED", "true")
        monkeypatch.setenv("MCP_SERVER_URL", server.url)
        yield server


@pytest.fixture
def fake_rag_server(monkeypatch):
    with FakeRagServer() as server:
        monkeypatch.setenv("RAG_ENABLED", "true")
        monkeypatch.setenv("RAG_SERVER_URL", server.url)
        yield server


@pytest.fixture
def fake_multi_agent_server(monkeypatch):
    with FakeMultiAgentServer() as server:
        monkeypatch.setenv("MULTI_AGENT_ENABLED", "true")
        monkeypatch.setenv("MULTI_AGENT_SERVER_URL", server.url)
        yield server
