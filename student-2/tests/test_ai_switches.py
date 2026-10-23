"""
test_ai_switches.py - proves the MCP, RAG and Multi-Agent on/off switches work,
and that they are independent of each other.

Release 1 needs the integrations OFF in CI, and the backend must say
"disabled" clearly. Two layers are tested:

  * settings.py     - which text values count as on / off.
  * the three routes - backend/routes/mcp_routes.py, rag_routes.py and
                      multi_agent_routes.py, in a real Flask test client,
                      against the fakes from helpers/fake_ai_servers.py (never a
                      real MCP / RAG / Multi-Agent server / Ollama).

The switches are the environment variables settings.py reads on every call
(MCP_ENABLED, RAG_ENABLED, MULTI_AGENT_ENABLED). Each test changes them with monkeypatch, which
puts the original values back when the test ends, so no test affects another.
Each fake runs on its own random free port and records every request it gets,
which is how "disabled means nothing is contacted" is proved.

Scenarios covered:
  1. MCP off: status + call read "disabled", fake MCP gets zero requests.
  2. RAG off: status + call read "disabled", fake RAG gets zero requests.
  3. Each switch on: requests reach its fake and succeed.
  4. Turning one switch off leaves the other one working.
  5. 1 / true / yes / on (any case) are on; False / 0 / empty / missing are off.
  6. Multi-Agent off: status reads "disabled", start is refused, and the fake
     Multi-Agent Server gets zero requests. Multi-Agent on: status reads "ok".
     It is independent of MCP and RAG, and its blueprint is really registered
     in backend/app.py (the status route answers 200, not 404).
"""

import importlib.util
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
from routes import multi_agent_routes as ma_module  # noqa: E402
from routes import rag_routes as rag_module  # noqa: E402
from helpers.fake_ai_servers import (  # noqa: E402
    CARE_TASKS_TOOL,
    OPEN_TASKS,
    fake_mcp_server,  # noqa: F401  (pytest fixtures, used by name below)
    fake_multi_agent_server,  # noqa: F401
    fake_rag_server,  # noqa: F401
)

DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}

MCP_CALL = "/api/mcp/call"
MCP_STATUS = "/api/mcp/status"
RAG_ASK = "/api/rag/ask"
RAG_STATUS = "/api/rag/status"
MA_START = "/api/multi-agent/start"
MA_STATUS = "/api/multi-agent/status"

# A valid request body for each call route.
MCP_BODY = {"tool": CARE_TASKS_TOOL, "arguments": {"admission_id": 40}}
RAG_BODY = {"question": "When is a care task overdue?"}
MA_BODY = {"admission_id": 1}


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------
@pytest.fixture
def client(monkeypatch, fake_mcp_server, fake_rag_server, fake_multi_agent_server):
    """
    Flask test client with all three blueprints mounted, calling as a doctor.
    The fake fixtures run first, so all three switches start ON and every
    server address already points at a fake. Tests then flip whichever switch
    they need; monkeypatch restores everything afterwards.
    """
    from flask import Flask

    # Short deadlines so a test can never hang.
    monkeypatch.setenv("MCP_TIMEOUT", "5")
    monkeypatch.setenv("RAG_TIMEOUT", "5")
    monkeypatch.setenv("MULTI_AGENT_TIMEOUT", "5")
    # Act as a clinical user (auth.CURRENT_USER is restored by monkeypatch).
    monkeypatch.setattr(auth, "CURRENT_USER", dict(DOCTOR))

    app = Flask(__name__)
    app.register_blueprint(mcp_module.mcp_bp, url_prefix="/api/mcp")
    app.register_blueprint(rag_module.rag_bp, url_prefix="/api/rag")
    app.register_blueprint(ma_module.multi_agent_bp, url_prefix="/api/multi-agent")
    app.config.update(TESTING=True)
    return app.test_client()


@pytest.fixture
def real_app_client(monkeypatch):
    """
    A test client for the REAL backend/app.py, to prove its blueprint table
    registers the multi-agent routes. app.py is loaded by file path under its
    own module name, because tests/test_care_task_form_frontend.py already
    imports the frontend's app.py as `app`.
    """
    spec = importlib.util.spec_from_file_location(
        "student2_backend_app_under_test", os.path.join(BACKEND_DIR, "app.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(auth, "CURRENT_USER", dict(DOCTOR))
    return module.app.test_client()


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


def _assert_multi_agent_disabled(client):
    """Multi-Agent start = 503 with a clear code, status = 200 "disabled"."""
    start = client.post(MA_START, json=MA_BODY)
    assert start.status_code == 503
    assert start.get_json() == {
        "status": "disabled",
        "error": {"code": "multi_agent_disabled", "message": "Multi-Agent review is not enabled."},
    }
    status = client.get(MA_STATUS)
    assert status.status_code == 200
    assert status.get_json() == {"status": "disabled"}


def _assert_multi_agent_status_ok(client, fake_ma):
    """Multi-Agent status says "ok" after a health probe that really reached the fake."""
    status = client.get(MA_STATUS)
    assert status.status_code == 200
    assert status.get_json() == {"status": "ok"}
    assert [(r.method, r.path) for r in fake_ma.requests] == [("GET", "/health")]


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


# ==========================================================================
# 6. Multi-Agent: off reads "disabled" and contacts nothing; on reads "ok";
#    independent of MCP and RAG; the blueprint is really registered.
# ==========================================================================
@pytest.mark.parametrize("how", ["false", "missing"])
def test_multi_agent_off_reads_disabled_and_contacts_nothing(
    client, fake_multi_agent_server, monkeypatch, how
):
    """Switched off (set to false, or not set at all): "disabled", zero requests."""
    if how == "missing":
        monkeypatch.delenv("MULTI_AGENT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("MULTI_AGENT_ENABLED", "false")

    _assert_multi_agent_disabled(client)

    assert fake_multi_agent_server.request_count == 0


def test_multi_agent_on_reaches_the_fake_and_status_is_ok(client, fake_multi_agent_server):
    """Multi-Agent is on (the fixture's default): /status probes the fake and says "ok"."""
    _assert_multi_agent_status_ok(client, fake_multi_agent_server)


def test_multi_agent_on_with_the_fake_stopped_reads_unavailable(client, fake_multi_agent_server):
    """On but nothing listening: "unavailable" with HTTP 200, never "disabled"."""
    fake_multi_agent_server.stop()

    resp = client.get(MA_STATUS)

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "unavailable"}


def test_multi_agent_off_does_not_affect_mcp_and_rag(
    client, fake_mcp_server, fake_rag_server, fake_multi_agent_server, monkeypatch
):
    """Multi-Agent off: it is disabled and untouched, MCP and RAG still work."""
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "false")

    _assert_multi_agent_disabled(client)
    _assert_mcp_works(client, fake_mcp_server)
    _assert_rag_works(client, fake_rag_server)

    assert fake_multi_agent_server.request_count == 0


@pytest.mark.parametrize("off", ["MCP_ENABLED", "RAG_ENABLED"])
def test_mcp_or_rag_off_does_not_affect_multi_agent(client, fake_multi_agent_server, monkeypatch, off):
    """Turning MCP or RAG off leaves Multi-Agent's status at "ok"."""
    monkeypatch.setenv(off, "false")

    _assert_multi_agent_status_ok(client, fake_multi_agent_server)


def test_all_three_off_contacts_no_fake(
    client, fake_mcp_server, fake_rag_server, fake_multi_agent_server, monkeypatch
):
    """All three off (as in CI): every route says "disabled"; no fake is touched."""
    for name in ("MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED"):
        monkeypatch.setenv(name, "false")

    _assert_mcp_disabled(client)
    _assert_rag_disabled(client)
    _assert_multi_agent_disabled(client)

    assert fake_mcp_server.request_count == 0
    assert fake_rag_server.request_count == 0
    assert fake_multi_agent_server.request_count == 0


def test_multi_agent_blueprint_is_registered_in_the_real_app(real_app_client, monkeypatch):
    """backend/app.py serves /api/multi-agent/status: 200 ("disabled" here), not 404."""
    monkeypatch.delenv("MULTI_AGENT_ENABLED", raising=False)

    resp = real_app_client.get(MA_STATUS)

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "disabled"}


def test_multi_agent_routes_are_all_registered_in_the_real_app(real_app_client, monkeypatch):
    """Each of the five routes exists under /api/multi-agent (switch off: 503, never 404)."""
    monkeypatch.delenv("MULTI_AGENT_ENABLED", raising=False)
    uuid_text = "11111111-2222-3333-4444-555555555555"

    answers = {
        "start": real_app_client.post(MA_START, json=MA_BODY).status_code,
        "progress": real_app_client.get("/api/multi-agent/workflows/" + uuid_text).status_code,
        "review": real_app_client.post(
            "/api/multi-agent/workflows/{}/review".format(uuid_text),
            json={"decision": "approve", "comment": ""},
        ).status_code,
        "latest": real_app_client.get("/api/multi-agent/latest?admission_id=1").status_code,
    }

    assert answers == {"start": 503, "progress": 503, "review": 503, "latest": 503}


def test_ai_off_returns_fallback_without_network(monkeypatch):
    from services import ollama_client

    monkeypatch.setattr(ollama_client, "AI_ENABLED", False)
    monkeypatch.setattr(
        ollama_client.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("disabled AI contacted Ollama"),
    )

    result = ollama_client.summarise_clinical_history(
        "Documented clinical facts", "clinical"
    )

    assert result == ollama_client.FALLBACK_SUMMARY


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
    pytest.param("MULTI_AGENT_ENABLED", settings.MULTI_AGENT_ENABLED, id="MULTI_AGENT"),
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
    for name in ("MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED"):
        # Either unset, or whatever the shell set it to - never a leftover
        # test value such as "On" / "YES" / "false" from the tests above.
        assert os.environ.get(name) == _ORIGINAL_ENV[name]


# Values present when this file was imported (before any test ran).
_ORIGINAL_ENV = {
    name: os.environ.get(name) for name in ("MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED")
}
