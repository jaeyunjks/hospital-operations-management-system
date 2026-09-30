"""
fake_ai_servers.py - tiny fake MCP and RAG servers for tests and CI.

Why: tests and GitHub Actions must never need the real shared MCP server, the
real RAG server or Ollama. These fakes run on localhost, on a random free port,
in a background thread, and answer with the same reply shapes the real servers
sent in the recent logged runs:

  * FakeMcpServer - speaks the MCP Streamable HTTP protocol (JSON replies), so
    the real `mcp` SDK client inside services/mcp_client.py can talk to it.
    Tools: homs_open_care_tasks (Student 2's) and homs_echo, using the shared
    {schema_version, ok, tool, data, error} envelope.
  * FakeRagServer - answers POST /query with the real grounded reply and the
    real insufficient-context reply (schema 1.0), and the real error body.

Both record every HTTP request they receive, and can be switched between
response modes at any time with set_mode().

Standard library only (plus pytest for the fixtures at the bottom).
"""

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
