"""
test_mcp_routes.py - behaviour tests for backend/routes/mcp_routes.py

Same pattern as the other route tests in this folder: the blueprint runs in a
real Flask test client. The MCP server is NEVER the real one - it is the
FakeMcpServer from helpers/fake_ai_servers.py, started on a random free port
for each test and stopped afterwards, so tests share nothing and need no fixed
port. The real services/mcp_client.py (and the real `mcp` SDK) talk to it over
localhost.

The on/off switch and server address are set through the environment
variables that backend/settings.py reads on every call (MCP_ENABLED,
MCP_SERVER_URL, MCP_TIMEOUT) - the fake_mcp_server fixture sets the first two.

Who is calling is set per test via the `as_user` fixture (auth.CURRENT_USER).

Scenarios covered:
  1. Doctor, nurse and specialist can each call the tool and get the tasks.
  2. Bad input (no body, missing / negative / text admission_id, unknown tool)
     gets a clear 400 and never reaches the fake server.
  3. A tool error such as not_found passes through with its code.
  4. A stopped fake server gives status "unavailable" without crashing.
  5. Receptionist and admin get 403 and the fake server records zero requests.
"""

import os
import sys

import pytest

# --- Make backend/ and tests/helpers importable ----------------------------
# mcp_routes.py does `import settings`, `from auth import ...` and
# `from services import mcp_client`; those only resolve with backend/ on
# sys.path. tests/ itself is added so `helpers.fake_ai_servers` resolves too.
TESTS_DIR = os.path.abspath(os.path.dirname(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(TESTS_DIR, os.pardir, "backend"))
for _path in (BACKEND_DIR, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import auth  # noqa: E402
from routes import mcp_routes as mcp_module  # noqa: E402
from helpers.fake_ai_servers import (  # noqa: E402
    CARE_TASKS_TOOL,
    OPEN_TASKS,
    fake_mcp_server,  # noqa: F401  (pytest fixture, used by name below)
)

# Users we switch between via auth.CURRENT_USER (clinical ones are seed data).
DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}
SPECIALIST = {"id": 3, "name": "Dr Emily Brown", "role": "specialist"}
NURSE = {"id": 7, "name": "James Wilson", "role": "nurse"}
RECEPTIONIST = {"id": 99, "name": "Pat Adams", "role": "receptionist"}
ADMIN = {"id": 100, "name": "Alex Admin", "role": "admin"}

ADMISSION_ID = 40
CALL_URL = "/api/mcp/call"
STATUS_URL = "/api/mcp/status"


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------
@pytest.fixture
def as_user(monkeypatch):
    """Setter that pins auth.CURRENT_USER. Tests call as_user(DOCTOR) etc."""
    def _set(user):
        monkeypatch.setattr(auth, "CURRENT_USER", dict(user))
        return user

    return _set


@pytest.fixture
def client(monkeypatch, fake_mcp_server):
    """
    Flask test client with only the mcp blueprint mounted (its own AuthError
    handler comes with it). fake_mcp_server is pulled in first so MCP_ENABLED
    and MCP_SERVER_URL already point at the fake when the app is built.
    """
    from flask import Flask

    # Short deadline so a test can never hang on a dead server.
    monkeypatch.setenv("MCP_TIMEOUT", "5")

    app = Flask(__name__)
    app.register_blueprint(mcp_module.mcp_bp, url_prefix="/api/mcp")
    app.config.update(TESTING=True)
    return app.test_client()


# ------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------
NO_BODY = object()  # marker: send the POST with no body at all


def _call_body(admission_id=ADMISSION_ID):
    """A valid request body for POST /call."""
    return {"tool": CARE_TASKS_TOOL, "arguments": {"admission_id": admission_id}}


def _post(client, body):
    """POST /call with a JSON body, or with nothing when body is NO_BODY."""
    if body is NO_BODY:
        return client.post(CALL_URL)
    return client.post(CALL_URL, json=body)


def _assert_error_shape(resp, status, code):
    """Every error reply is {"status": ..., "error": {"code", "message"}}."""
    body = resp.get_json()
    assert body["status"] == status
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body


# ==========================================================================
# 1. Doctor, nurse and specialist can each call the tool and get the tasks.
# ==========================================================================
@pytest.mark.parametrize("user", [DOCTOR, NURSE, SPECIALIST], ids=lambda u: u["role"])
def test_clinical_roles_receive_the_tasks(client, fake_mcp_server, as_user, user):
    """
    Each clinical role gets 200 / status "ok" with the fake's open tasks, and
    the fake saw exactly one tool call: our tool, with our admission id.
    """
    as_user(user)

    resp = _post(client, _call_body())

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["tool"] == CARE_TASKS_TOOL
    assert body["result"]["admission_id"] == ADMISSION_ID
    assert body["result"]["count"] == len(OPEN_TASKS)
    assert body["result"]["tasks"] == OPEN_TASKS

    # The tool was called once, with only the admission id.
    assert fake_mcp_server.tool_calls == [
        {"name": CARE_TASKS_TOOL, "arguments": {"admission_id": ADMISSION_ID}}
    ]


# ==========================================================================
# 2. Bad input -> a clear 400, and nothing is sent to the fake server.
# ==========================================================================
# (body, text the message must mention so the caller knows what to fix)
BAD_BODIES = [
    pytest.param(NO_BODY, "JSON object", id="no_body"),
    pytest.param([1, 2, 3], "JSON object", id="body_is_a_list"),
    pytest.param({"tool": CARE_TASKS_TOOL, "arguments": {}}, "admission_id", id="missing_admission_id"),
    pytest.param(_call_body(-5), "admission_id", id="negative"),
    pytest.param(_call_body(0), "admission_id", id="zero"),
    pytest.param(_call_body("abc"), "admission_id", id="text"),
    pytest.param(_call_body("5"), "admission_id", id="number_as_text"),
    pytest.param(_call_body(True), "admission_id", id="boolean"),
    pytest.param(_call_body(1.5), "admission_id", id="decimal"),
    pytest.param({"tool": "homs_echo", "arguments": {"message": "hi"}}, "tool", id="unknown_tool_name"),
    pytest.param({"arguments": {"admission_id": 1}}, "tool", id="missing_tool_name"),
    pytest.param({"tool": CARE_TASKS_TOOL}, "arguments", id="missing_arguments"),
]


@pytest.mark.parametrize("body, hint", BAD_BODIES)
def test_bad_input_is_a_clear_400(client, fake_mcp_server, as_user, body, hint):
    """
    Bad input is refused with 400 / code "invalid_request" and a message that
    names the problem. The fake server records zero requests, so the bad
    input was stopped before the MCP client was used.
    """
    as_user(DOCTOR)

    resp = _post(client, body)

    assert resp.status_code == 400
    error = _assert_error_shape(resp, "error", "invalid_request")["error"]
    assert hint in error["message"]
    assert fake_mcp_server.request_count == 0


# ==========================================================================
# 3. A tool error passes through with its code (and a fixed message).
# ==========================================================================
def test_tool_error_not_found_passes_through(client, fake_mcp_server, as_user):
    """The tool says not_found -> HTTP 404 with the same code in the body."""
    as_user(NURSE)
    fake_mcp_server.set_mode("tool_error", code="not_found")

    resp = _post(client, _call_body())

    assert resp.status_code == 404
    _assert_error_shape(resp, "error", "not_found")
    assert fake_mcp_server.tool_call_count == 1


@pytest.mark.parametrize(
    "code, http_status",
    [
        ("invalid_input", 400),
        ("upstream_unavailable", 503),
        ("something_new", 502),   # a code the route does not know -> 502
    ],
)
def test_other_tool_error_codes_keep_their_code(client, fake_mcp_server, as_user, code, http_status):
    """Each tool code is passed through; only the HTTP status is chosen by the route."""
    as_user(DOCTOR)
    fake_mcp_server.set_mode("tool_error", code=code)

    resp = _post(client, _call_body())

    assert resp.status_code == http_status
    _assert_error_shape(resp, "error", code)


def test_tool_error_text_is_not_leaked(client, fake_mcp_server, as_user):
    """Upstream message text and details never reach the caller."""
    as_user(DOCTOR)
    fake_mcp_server.set_mode(
        "tool_error", code="not_found",
        message="SECRET upstream wording", details={"host": "student-2-db:5000"},
    )

    resp = _post(client, _call_body())

    assert "SECRET" not in resp.get_data(as_text=True)
    assert "student-2-db" not in resp.get_data(as_text=True)


# ==========================================================================
# 4. Fake server stopped -> "unavailable", no crash.
# ==========================================================================
def test_stopped_server_call_reports_unavailable(client, fake_mcp_server, as_user):
    """Nothing is listening any more: a clean 503 "unavailable", not a 500."""
    as_user(DOCTOR)
    port = fake_mcp_server.port  # read before stop() clears it
    fake_mcp_server.stop()

    resp = _post(client, _call_body())

    assert resp.status_code == 503
    body = _assert_error_shape(resp, "unavailable", "mcp_unavailable")
    # No address or traceback in the reply.
    text = resp.get_data(as_text=True)
    assert str(port) not in text
    assert "Traceback" not in text
    assert body["error"]["message"] == "The MCP server is temporarily unavailable."


def test_stopped_server_status_reports_unavailable(client, fake_mcp_server, as_user):
    """GET /status is always 200 for clinical roles; the state is the answer."""
    as_user(DOCTOR)
    fake_mcp_server.stop()

    resp = client.get(STATUS_URL)

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "unavailable", "tool": CARE_TASKS_TOOL}


def test_status_ok_when_server_lists_the_tool(client, fake_mcp_server, as_user):
    """Control for the test above: with the fake running, /status says "ok"."""
    as_user(DOCTOR)

    resp = client.get(STATUS_URL)

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "tool": CARE_TASKS_TOOL}


# ==========================================================================
# 5. Receptionist and admin -> 403, and the fake server sees nothing.
# ==========================================================================
@pytest.mark.parametrize("user", [RECEPTIONIST, ADMIN], ids=lambda u: u["role"])
def test_blocked_roles_get_403_and_server_sees_nothing(client, fake_mcp_server, as_user, user):
    """
    A non-clinical role is refused with 403 on both endpoints, even with a
    perfectly valid body, and the fake server records zero requests.
    """
    as_user(user)

    call_resp = _post(client, _call_body())
    status_resp = client.get(STATUS_URL)

    assert call_resp.status_code == 403
    _assert_error_shape(call_resp, "error", "forbidden")
    assert status_resp.status_code == 403
    _assert_error_shape(status_resp, "error", "forbidden")
    assert fake_mcp_server.request_count == 0


@pytest.mark.parametrize("switch", ["true", "false"])
def test_blocked_role_reply_does_not_depend_on_the_switch(
    client, fake_mcp_server, as_user, monkeypatch, switch
):
    """A blocked role gets the same 403 whether MCP is on or off (nothing leaks)."""
    as_user(RECEPTIONIST)
    monkeypatch.setenv("MCP_ENABLED", switch)

    resp = _post(client, _call_body())

    assert resp.status_code == 403
    _assert_error_shape(resp, "error", "forbidden")
    assert fake_mcp_server.request_count == 0


# ==========================================================================
# Extra: the switch itself (set through settings' own env variable).
# ==========================================================================
def test_switch_off_gives_disabled_and_contacts_nothing(client, fake_mcp_server, as_user, monkeypatch):
    """MCP_ENABLED=false -> 503 "disabled"; the fake server records zero requests."""
    as_user(DOCTOR)
    monkeypatch.setenv("MCP_ENABLED", "false")

    resp = _post(client, _call_body())

    assert resp.status_code == 503
    _assert_error_shape(resp, "disabled", "mcp_disabled")
    assert fake_mcp_server.request_count == 0
