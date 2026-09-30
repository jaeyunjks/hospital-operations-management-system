"""
test_rag_routes.py - behaviour tests for backend/routes/rag_routes.py

Same pattern as the other route tests in this folder: the blueprint runs in a
real Flask test client, but every outbound dependency is replaced.

  * The RAG server -> FakeRagServer from helpers/fake_ai_servers.py, on a random
                      free port per test. The real services/rag_client.py talks
                      to it over localhost. Its "normal" reply is the real
                      grounded answer from the recent RAG validation log
                      (care-task escalation, confidence "medium", score 0.7034);
                      its "insufficient" mode is the real refusal reply
                      (confidence "none", top score 0.5014).
  * services.database_client -> a MagicMock (no socket to the DB container),
                      exactly as test_ai_summary.py does it. The route's log
                      call is db.create_ai_summary, so that is what we inspect.

The on/off switch, address and timeout are the environment variables
backend/settings.py reads on every call; the fake_rag_server fixture sets the
first two, and monkeypatch restores them after each test.

Who is calling is set per test via the `as_user` fixture (auth.CURRENT_USER).

Scenarios covered:
  1. Grounded answer: answer, source names, confidence word and score returned.
  2. Insufficient-context reply: returned as such, and no log row is written.
  3. Empty / whitespace-only / over-500-character question: clear 400.
  4. Fake stopped or slow: "unavailable", no crash.
  5. Receptionist and admin: 403, zero requests reach the fake.
  6. Grounded answer logs exactly one ai_summaries row: source_documents is the
     list of source names, chosen scope, review 'pending', and the question
     text appears in none of the stored fields.
  7. A failing log call still returns the answer, with logged false.
"""

import logging
import os
import sys
from unittest.mock import MagicMock

import pytest

# --- Make backend/ and tests/helpers importable ----------------------------
# rag_routes.py does `import settings`, `from auth import ...`,
# `from routes.ai_summary import ...` and `from services import ...`; those
# only resolve with backend/ on sys.path.
TESTS_DIR = os.path.abspath(os.path.dirname(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(TESTS_DIR, os.pardir, "backend"))
for _path in (BACKEND_DIR, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import auth  # noqa: E402
from routes import rag_routes as rag_module  # noqa: E402
from helpers.fake_ai_servers import (  # noqa: E402
    RAG_INSUFFICIENT_MESSAGE,
    RAG_MODELS,
    fake_rag_server,  # noqa: F401  (pytest fixture, used by name below)
    grounded_reply,
)

# Seed-data users we switch between via auth.CURRENT_USER.
DOCTOR = {"id": 1, "name": "Dr Daniel Chen", "role": "doctor"}
SPECIALIST = {"id": 3, "name": "Dr Emily Brown", "role": "specialist"}
NURSE = {"id": 7, "name": "James Wilson", "role": "nurse"}
RECEPTIONIST = {"id": 99, "name": "Pat Adams", "role": "receptionist"}
ADMIN = {"id": 100, "name": "Alex Admin", "role": "admin"}

ASK_URL = "/api/rag/ask"
QUESTION = "A task has sat there for over an hour and nobody has picked it up. What should I do?"
ADMISSION_ID = 40
PATIENT_ID = 55

# What the fake's normal (grounded) reply contains - from the real logged run.
GROUNDED_SOURCES = ["student-2/care-task-escalation.md"]
GROUNDED_CONFIDENCE = "medium"
GROUNDED_SCORE = 0.7034
GROUNDED_ANSWER = grounded_reply(QUESTION, "student-2")["answer"]
INSUFFICIENT_SCORE = 0.5014

# The scope each role gets by default (the route reuses ai_summary's rule).
SCOPE_FOR_ROLE = {"doctor": "clinical", "specialist": "consultation", "nurse": "care_tasks"}


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------
@pytest.fixture
def db_mock(monkeypatch):
    """
    Replace the database_client reference the route holds with a MagicMock. The
    real DatabaseClientError class is copied on, as in test_ai_summary.py.
    create_ai_summary echoes the payload back with a generated id (7).
    """
    mock = MagicMock(name="database_client")
    mock.DatabaseClientError = rag_module.db.DatabaseClientError
    mock.create_ai_summary.side_effect = lambda payload: {"summary_id": 7, **payload}
    monkeypatch.setattr(rag_module, "db", mock)
    return mock


@pytest.fixture
def as_user(monkeypatch):
    """Setter that pins auth.CURRENT_USER. Tests call as_user(DOCTOR) etc."""
    def _set(user):
        monkeypatch.setattr(auth, "CURRENT_USER", dict(user))
        return user

    return _set


@pytest.fixture
def client(monkeypatch, fake_rag_server, db_mock):
    """
    Flask test client with only the rag blueprint mounted (its own AuthError
    handler comes with it). fake_rag_server runs first, so RAG_ENABLED and
    RAG_SERVER_URL already point at the fake; db_mock is patched before the
    app is built.
    """
    from flask import Flask

    # Short deadline so a dead or slow fake can never hang a test.
    monkeypatch.setenv("RAG_TIMEOUT", "5")

    app = Flask(__name__)
    app.register_blueprint(rag_module.rag_bp, url_prefix="/api/rag")
    app.config.update(TESTING=True)
    return app.test_client()


# ------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------
def _ask(client, question=QUESTION, **ids):
    """POST /ask; extra keyword arguments (admission_id, patient_id) join the body."""
    return client.post(ASK_URL, json={"question": question, **ids})


def _assert_error(resp, http_status, status, code):
    """Every error reply is {"status": ..., "error": {"code", "message"}}."""
    assert resp.status_code == http_status
    body = resp.get_json()
    assert body["status"] == status
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body


# ==========================================================================
# 1. Grounded answer: answer, source names, confidence word and score.
# ==========================================================================
@pytest.mark.parametrize("user", [DOCTOR, NURSE, SPECIALIST], ids=lambda u: u["role"])
def test_grounded_answer_returns_answer_sources_confidence_score(
    client, fake_rag_server, db_mock, as_user, user
):
    """A clinical role gets the fake's grounded answer, with the four fields exposed."""
    as_user(user)

    resp = _ask(client)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "answered"
    assert body["answer"] == GROUNDED_ANSWER
    assert body["sources"] == GROUNDED_SOURCES
    assert body["confidence"] == GROUNDED_CONFIDENCE
    assert body["score"] == GROUNDED_SCORE

    # No ids were given, so nothing is logged.
    assert body["logged"] is False
    assert body["summary_id"] is None
    db_mock.create_ai_summary.assert_not_called()

    # Only the question (and the feature name) went to the RAG server.
    assert fake_rag_server.queries == [{"question": QUESTION, "feature": "student-2"}]


# ==========================================================================
# 2. Insufficient context: returned as such, and no log row is written.
# ==========================================================================
def test_insufficient_context_is_returned_and_not_logged(
    client, fake_rag_server, db_mock, as_user
):
    """
    The fake says it has nothing relevant. The route passes that on (200,
    "insufficient_context", no sources, confidence "none") and - even with both
    ids supplied - writes no ai_summaries row.
    """
    as_user(DOCTOR)
    fake_rag_server.set_mode("insufficient")

    resp = _ask(client, admission_id=ADMISSION_ID, patient_id=PATIENT_ID)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "insufficient_context"
    assert body["answer"] == RAG_INSUFFICIENT_MESSAGE
    assert body["sources"] == []
    assert body["confidence"] == "none"
    assert body["score"] == INSUFFICIENT_SCORE
    assert body["logged"] is False
    assert body["summary_id"] is None

    # The fake was asked, but nothing was logged.
    assert fake_rag_server.query_count == 1
    db_mock.create_ai_summary.assert_not_called()


# ==========================================================================
# 3. Bad question -> a clear 400, nothing sent to the fake, nothing logged.
# ==========================================================================
# (question, text the message must mention so the caller knows what to fix)
BAD_QUESTIONS = [
    pytest.param("", "non-empty", id="empty"),
    pytest.param("   \n\t  ", "non-empty", id="whitespace_only"),
    pytest.param("x" * 501, "500", id="over_500_characters"),
    pytest.param("  " + "x" * 501 + "  ", "500", id="over_500_even_after_trimming"),
    pytest.param(None, "non-empty", id="null"),
    pytest.param(123, "non-empty", id="number"),
]


@pytest.mark.parametrize("question, hint", BAD_QUESTIONS)
def test_bad_question_is_a_clear_400(client, fake_rag_server, db_mock, as_user, question, hint):
    """Refused with 400 / "invalid_request" and a message naming 'question'."""
    as_user(DOCTOR)

    resp = _ask(client, question, admission_id=ADMISSION_ID, patient_id=PATIENT_ID)

    body = _assert_error(resp, 400, "error", "invalid_request")
    assert "'question'" in body["error"]["message"]
    assert hint in body["error"]["message"]
    # Stopped before the client was used and before any log write.
    assert fake_rag_server.request_count == 0
    db_mock.create_ai_summary.assert_not_called()


def test_question_of_exactly_500_characters_is_accepted(client, fake_rag_server, as_user):
    """Boundary: 500 is the longest allowed question (padding spaces are trimmed first)."""
    as_user(DOCTOR)
    question = "x" * 500

    resp = _ask(client, "  " + question + "  ")

    assert resp.status_code == 200
    # The trimmed text is what the fake received.
    assert fake_rag_server.queries == [{"question": question, "feature": "student-2"}]


# ==========================================================================
# 4. Fake stopped or slow -> "unavailable", no crash.
# ==========================================================================
def _assert_unavailable(resp, fake_port, db_mock):
    """A clean 503 "unavailable" with fixed text: no address, traceback or log row."""
    body = _assert_error(resp, 503, "unavailable", "rag_unavailable")
    assert body["error"]["message"] == "The policy assistant is temporarily unavailable."
    text = resp.get_data(as_text=True)
    assert str(fake_port) not in text
    assert "Traceback" not in text
    db_mock.create_ai_summary.assert_not_called()


def test_stopped_server_reports_unavailable(client, fake_rag_server, db_mock, as_user):
    """Nothing is listening any more: 503 "unavailable", not a 500."""
    as_user(DOCTOR)
    port = fake_rag_server.port  # read before stop() clears it
    fake_rag_server.stop()

    resp = _ask(client, admission_id=ADMISSION_ID, patient_id=PATIENT_ID)

    _assert_unavailable(resp, port, db_mock)


def test_slow_server_reports_unavailable(client, fake_rag_server, db_mock, as_user, monkeypatch):
    """The fake takes 3 s but we only wait 0.5 s: the timeout reads as "unavailable"."""
    as_user(DOCTOR)
    monkeypatch.setenv("RAG_TIMEOUT", "0.5")
    fake_rag_server.set_mode("slow", delay=3)

    resp = _ask(client, admission_id=ADMISSION_ID, patient_id=PATIENT_ID)

    _assert_unavailable(resp, fake_rag_server.port, db_mock)
    assert fake_rag_server.query_count == 1  # the request did reach the fake


def test_server_error_reports_unavailable(client, fake_rag_server, db_mock, as_user):
    """Extra: the fake answering 503 (Ollama down) is also "unavailable"."""
    as_user(DOCTOR)
    fake_rag_server.set_mode("server_error", status=503)

    resp = _ask(client)

    _assert_unavailable(resp, fake_rag_server.port, db_mock)


# ==========================================================================
# 5. Receptionist and admin -> 403, and the fake sees nothing.
# ==========================================================================
@pytest.mark.parametrize("user", [RECEPTIONIST, ADMIN], ids=lambda u: u["role"])
def test_blocked_roles_get_403_and_fake_sees_nothing(
    client, fake_rag_server, db_mock, as_user, user
):
    """A non-clinical role is refused with 403, even with a perfectly valid body."""
    as_user(user)

    resp = _ask(client, admission_id=ADMISSION_ID, patient_id=PATIENT_ID)

    _assert_error(resp, 403, "error", "forbidden")
    assert fake_rag_server.request_count == 0
    db_mock.create_ai_summary.assert_not_called()


# ==========================================================================
# 6. A grounded answer creates exactly one ai_summaries log row.
# ==========================================================================
@pytest.mark.parametrize("user", [DOCTOR, SPECIALIST, NURSE], ids=lambda u: u["role"])
def test_grounded_answer_is_logged_once_with_role_scope(
    client, fake_rag_server, db_mock, as_user, user
):
    """
    One db.create_ai_summary call. source_documents is the real list of source
    names; summary_scope is the role's default; review_status is 'pending';
    source_reference carries the requester stamp; the response reports the row.
    """
    as_user(user)

    resp = _ask(client, admission_id=ADMISSION_ID, patient_id=PATIENT_ID)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["logged"] is True
    assert body["summary_id"] == 7

    db_mock.create_ai_summary.assert_called_once()
    logged = db_mock.create_ai_summary.call_args.args[0]
    assert logged["admission_id"] == ADMISSION_ID
    assert logged["patient_id"] == PATIENT_ID
    assert logged["summary_text"] == GROUNDED_ANSWER
    assert logged["source_documents"] == GROUNDED_SOURCES
    assert isinstance(logged["source_documents"], list)  # a real list, not text
    assert logged["summary_scope"] == SCOPE_FOR_ROLE[user["role"]]
    assert logged["review_status"] == "pending"
    assert logged["source_reference"] == "requested_by_staff_id={}".format(user["id"])
    assert logged["model_used"] == RAG_MODELS["generation"]


def test_logged_fields_never_contain_the_question(client, fake_rag_server, db_mock, as_user):
    """
    The clinician may type patient details into the question, so it must not
    be stored anywhere. Checks every stored field (and every word of the
    question) against what create_ai_summary received.
    """
    as_user(DOCTOR)
    question = "Jane Citizen in bed 4 has an overdue wound dressing - who do I tell?"

    resp = _ask(client, question, admission_id=ADMISSION_ID, patient_id=PATIENT_ID)

    assert resp.status_code == 200
    assert fake_rag_server.queries[0]["question"] == question  # it did reach the fake
    logged = db_mock.create_ai_summary.call_args.args[0]

    # No field holds the question, or the identifying words in it.
    stored_text = " ".join(str(value) for value in logged.values()).lower()
    assert question.lower() not in stored_text
    for word in ("jane", "citizen", "bed 4", "wound dressing"):
        assert word not in stored_text
    # And no field is named after it.
    assert "question" not in logged


@pytest.mark.parametrize(
    "ids",
    [{"admission_id": ADMISSION_ID}, {"patient_id": PATIENT_ID}, {}],
    ids=["admission_only", "patient_only", "no_ids"],
)
def test_no_log_row_unless_both_ids_are_given(client, fake_rag_server, db_mock, as_user, ids):
    """Extra: a row needs both ids; with fewer the answer still comes back, unlogged."""
    as_user(DOCTOR)

    resp = _ask(client, **ids)

    assert resp.status_code == 200
    assert resp.get_json()["logged"] is False
    db_mock.create_ai_summary.assert_not_called()


# ==========================================================================
# 7. A failing log call still returns the answer, with logged false.
# ==========================================================================
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(
            lambda db: db.DatabaseClientError("could not reach database API at http://secret-db:5000"),
            id="database_client_error",
        ),
        pytest.param(lambda db: RuntimeError("secret-db exploded"), id="unexpected_error"),
    ],
)
def test_failing_log_call_still_returns_the_answer(
    client, fake_rag_server, db_mock, as_user, caplog, failure
):
    """
    The log write raises. The clinician still gets the grounded answer with
    logged false and no summary_id, and neither the reply nor the log output
    contains the failure text (it can hold the database address).
    """
    as_user(DOCTOR)
    db_mock.create_ai_summary.side_effect = failure(db_mock)

    with caplog.at_level(logging.DEBUG):
        resp = _ask(client, admission_id=ADMISSION_ID, patient_id=PATIENT_ID)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "answered"
    assert body["answer"] == GROUNDED_ANSWER
    assert body["sources"] == GROUNDED_SOURCES
    assert body["logged"] is False
    assert body["summary_id"] is None

    db_mock.create_ai_summary.assert_called_once()  # it was attempted
    assert "secret-db" not in resp.get_data(as_text=True)
    assert "secret-db" not in caplog.text
