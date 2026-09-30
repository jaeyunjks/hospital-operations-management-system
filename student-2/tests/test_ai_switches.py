"""
test_ai_switches.py - proves the MCP and RAG on/off switches work, and that
they are independent of each other.

Release 1 needs both integrations OFF in CI, and the backend must say
"disabled" clearly. Two layers are tested:

  * settings.py     - which text values count as on / off.
  * the two routes  - backend/routes/mcp_routes.py and rag_routes.py, in a real
                      Flask test client, against the fakes from
                      helpers/fake_ai_servers.py (never a real MCP / RAG / Ollama).

The switches are the environment variables settings.py reads on every call
(MCP_ENABLED, RAG_ENABLED). Each test changes them with monkeypatch, which
puts the original values back when the test ends, so no test affects another.
Each fake runs on its own random free port and records every request it gets,
which is how "disabled means nothing is contacted" is proved.

Scenarios covered:
  1. MCP off: status + call read "disabled", fake MCP gets zero requests.
  2. RAG off: status + call read "disabled", fake RAG gets zero requests.
  3. Each switch on: requests reach its fake and succeed.
  4. Turning one switch off leaves the other one working.
  5. 1 / true / yes / on (any case) are on; False / 0 / empty / missing are off.
"""

import os
import sys

import pytest

# --- Make backend/ and tests/helpers importable ----------------------------
# The routes do `import settings`, `from auth import ...` and
# `from services import ...`; those only resolve with backend/ on sys.path.
TESTS_DIR = os.path.abspath(os.path.dirname(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(TESTS_DIR, os.pardir, "backend"))
for _path in (BACKEND_DIR, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import auth  # noqa: E402
import settings  # noqa: E402
from routes import mcp_routes as mcp_module  # noqa: E402
from routes import rag_routes as rag_module  # noqa: E402
from helpers.fake_ai_servers import (  # noqa: E402
    CARE_TASKS_TOOL,
    OPEN_TASKS,
    fake_mcp_server,  # noqa: F401  (pytest fixtures, used by name below)
    fake_rag_server,  # noqa: F401
)

DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}

MCP_CALL = "/api/mcp/call"
MCP_STATUS = "/api/mcp/status"
RAG_ASK = "/api/rag/ask"
RAG_STATUS = "/api/rag/status"

# A valid request body for each call route.
MCP_BODY = {"tool": CARE_TASKS_TOOL, "arguments": {"admission_id": 40}}
RAG_BODY = {"question": "When is a care task overdue?"}


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------
@pytest.fixture
def client(monkeypatch, fake_mcp_server, fake_rag_server):
    """
    Flask test client with both blueprints mounted, calling as a doctor.
    Both fake fixtures run first, so both switches start ON and both server
    addresses already point at the fakes. Tests then flip whichever switch
    they need; monkeypatch restores everything afterwards.
    """
    from flask import Flask

    # Short deadlines so a test can never hang.
    monkeypatch.setenv("MCP_TIMEOUT", "5")
    monkeypatch.setenv("RAG_TIMEOUT", "5")
    # Act as a clinical user (auth.CURRENT_USER is restored by monkeypatch).
    monkeypatch.setattr(auth, "CURRENT_USER", dict(DOCTOR))

    app = Flask(__name__)
    app.register_blueprint(mcp_module.mcp_bp, url_prefix="/api/mcp")
    app.register_blueprint(rag_module.rag_bp, url_prefix="/api/rag")
    app.config.update(TESTING=True)
    return app.test_client()


# ------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------
def _assert_mcp_disabled(client):
    """Both MCP routes say "disabled" (call = 503 with a clear code, status = 200)."""
    call = client.post(MCP_CALL, json=MCP_BODY)
    assert call.status_code == 503
    assert call.get_json() == {
        "status": "disabled",
        "error": {"code": "mcp_disabled", "message": "MCP is not enabled."},
    }
    status = client.get(MCP_STATUS)
    assert status.status_code == 200
    assert status.get_json() == {"status": "disabled", "tool": CARE_TASKS_TOOL}


def _assert_rag_disabled(client):
    """Both RAG routes say "disabled" (ask = 503 with a clear code, status = 200)."""
    ask = client.post(RAG_ASK, json=RAG_BODY)
    assert ask.status_code == 503
    assert ask.get_json() == {
        "status": "disabled",
        "error": {"code": "rag_disabled", "message": "RAG is not enabled."},
    }
    status = client.get(RAG_STATUS)
    assert status.status_code == 200
    assert status.get_json() == {"status": "disabled"}


def _assert_mcp_works(client, fake_mcp):
    """The MCP call succeeds and the fake really received it."""
    resp = client.post(MCP_CALL, json=MCP_BODY)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["result"]["tasks"] == OPEN_TASKS
    assert fake_mcp.tool_call_count == 1


def _assert_rag_works(client, fake_rag):
    """The RAG question is answered and the fake really received it."""
    resp = client.post(RAG_ASK, json=RAG_BODY)
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "answered"
    assert fake_rag.query_count == 1


# ==========================================================================
# 1. MCP off: "disabled" on both routes, and the fake MCP server sees nothing.
# ==========================================================================
@pytest.mark.parametrize("how", ["false", "missing"])
def test_mcp_off_reads_disabled_and_contacts_nothing(
    client, fake_mcp_server, monkeypatch, how
):
    """Switched off (set to false, or not set at all): "disabled", zero requests."""
    if how == "missing":
        monkeypatch.delenv("MCP_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MCP_ENABLED", "false")

    _assert_mcp_disabled(client)

    assert fake_mcp_server.request_count == 0


# ==========================================================================
# 2. RAG off: "disabled" on both routes, and the fake RAG server sees nothing.
# ==========================================================================
@pytest.mark.parametrize("how", ["false", "missing"])
def test_rag_off_reads_disabled_and_contacts_nothing(
    client, fake_rag_server, monkeypatch, how
):
    """Switched off (set to false, or not set at all): "disabled", zero requests."""
    if how == "missing":
        monkeypatch.delenv("RAG_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RAG_ENABLED", "false")

    _assert_rag_disabled(client)

    assert fake_rag_server.request_count == 0


# ==========================================================================
# 3. Each switch on: requests reach its fake and succeed.
# ==========================================================================
def test_mcp_on_reaches_the_fake_and_succeeds(client, fake_mcp_server):
    """MCP is on (the fixture's default): the call succeeds and /status says "ok"."""
    _assert_mcp_works(client, fake_mcp_server)

    status = client.get(MCP_STATUS)
    assert status.status_code == 200
    assert status.get_json() == {"status": "ok", "tool": CARE_TASKS_TOOL}


def test_rag_on_reaches_the_fake_and_succeeds(client, fake_rag_server):
    """RAG is on (the fixture's default): the question is answered by the fake."""
    _assert_rag_works(client, fake_rag_server)

    # The fake got the question, tagged with this feature, and nothing else.
    assert fake_rag_server.queries == [
        {"question": RAG_BODY["question"], "feature": "student-2"}
    ]


def test_rag_status_on_contacts_the_fake_and_is_not_disabled(client, fake_rag_server):
    """
    With RAG on, GET /status must reach the fake (a health probe) and must not
    say "disabled". (The fake has no health page, so the exact state word is
    not asserted here - only what the switch controls.)
    """
    resp = client.get(RAG_STATUS)

    assert resp.status_code == 200
    assert resp.get_json()["status"] != "disabled"
    assert [(r.method, r.path) for r in fake_rag_server.requests] == [("GET", "/health")]


# ==========================================================================
# 4. Switches are independent: turning one off does not touch the other.
# ==========================================================================
def test_mcp_off_does_not_affect_rag(client, fake_mcp_server, fake_rag_server, monkeypatch):
    """MCP off: MCP is disabled and untouched, RAG still answers normally."""
    monkeypatch.setenv("MCP_ENABLED", "false")

    _assert_mcp_disabled(client)
    _assert_rag_works(client, fake_rag_server)

    assert fake_mcp_server.request_count == 0


def test_rag_off_does_not_affect_mcp(client, fake_mcp_server, fake_rag_server, monkeypatch):
    """RAG off: RAG is disabled and untouched, MCP still works normally."""
    monkeypatch.setenv("RAG_ENABLED", "false")

    _assert_rag_disabled(client)
    _assert_mcp_works(client, fake_mcp_server)

    assert fake_rag_server.request_count == 0


def test_both_off_contacts_neither_fake(client, fake_mcp_server, fake_rag_server, monkeypatch):
    """Both off (as in CI): all four routes say "disabled"; neither fake is touched."""
    monkeypatch.setenv("MCP_ENABLED", "false")
    monkeypatch.setenv("RAG_ENABLED", "false")

    _assert_mcp_disabled(client)
    _assert_rag_disabled(client)

    assert fake_mcp_server.request_count == 0
    assert fake_rag_server.request_count == 0


def test_switch_settings_read_separate_variables(monkeypatch):
    """At settings level: each function follows only its own variable."""
    monkeypatch.setenv("MCP_ENABLED", "true")
    monkeypatch.setenv("RAG_ENABLED", "false")
    assert settings.MCP_ENABLED() is True
    assert settings.RAG_ENABLED() is False

    # Swap them: the answers swap too (nothing is cached between calls).
    monkeypatch.setenv("MCP_ENABLED", "false")
    monkeypatch.setenv("RAG_ENABLED", "true")
    assert settings.MCP_ENABLED() is False
    assert settings.RAG_ENABLED() is True


# ==========================================================================
# 5. Which values count as on, and which as off (for both switches).
# ==========================================================================
SWITCH_FUNCTIONS = [
    pytest.param("MCP_ENABLED", settings.MCP_ENABLED, id="MCP"),
    pytest.param("RAG_ENABLED", settings.RAG_ENABLED, id="RAG"),
]

ON_VALUES = ["1", "true", "TRUE", "True", "yes", "YES", "Yes", "on", "ON", "On"]
OFF_VALUES = ["False", "false", "FALSE", "0", "", "no", "off"]


@pytest.mark.parametrize("name, read_switch", SWITCH_FUNCTIONS)
@pytest.mark.parametrize("value", ON_VALUES)
def test_on_values_count_as_on(monkeypatch, name, read_switch, value):
    """1 / true / yes / on, in any case, switch the feature on."""
    monkeypatch.setenv(name, value)

    assert read_switch() is True


@pytest.mark.parametrize("name, read_switch", SWITCH_FUNCTIONS)
@pytest.mark.parametrize("value", OFF_VALUES)
def test_off_values_count_as_off(monkeypatch, name, read_switch, value):
    """False / 0 / empty (and no / off) leave the feature off."""
    monkeypatch.setenv(name, value)

    assert read_switch() is False


@pytest.mark.parametrize("name, read_switch", SWITCH_FUNCTIONS)
def test_missing_variable_counts_as_off(monkeypatch, name, read_switch):
    """A variable that is not set at all means off (the safe default)."""
    monkeypatch.delenv(name, raising=False)

    assert read_switch() is False


def test_on_value_in_any_case_works_through_the_routes(
    client, fake_mcp_server, fake_rag_server, monkeypatch
):
    """The text really drives the routes: "On" / "YES" let requests through."""
    monkeypatch.setenv("MCP_ENABLED", "On")
    monkeypatch.setenv("RAG_ENABLED", "YES")

    _assert_mcp_works(client, fake_mcp_server)
    _assert_rag_works(client, fake_rag_server)


def test_switches_are_restored_after_a_test():
    """
    Guard for the "restore afterwards" rule: earlier tests changed both
    variables, yet nothing they set is still in the environment here unless it
    was already set before the run (the fixtures clean up after themselves).
    """
    for name in ("MCP_ENABLED", "RAG_ENABLED"):
        # Either unset, or whatever the shell set it to - never a leftover
        # test value such as "On" / "YES" / "false" from the tests above.
        assert os.environ.get(name) == _ORIGINAL_ENV[name]


# Values present when this file was imported (before any test ran).
_ORIGINAL_ENV = {name: os.environ.get(name) for name in ("MCP_ENABLED", "RAG_ENABLED")}
