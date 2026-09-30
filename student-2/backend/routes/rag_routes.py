"""
rag_routes.py - Flask Blueprint for policy questions answered by the shared RAG server.

Mount at /api/rag in app.py:

    POST /ask      ask a policy question; grounded answers are logged for review
    GET  /status   "disabled" | "ok" | "unavailable"

DESIGN RULES:

  * Order of checks on POST /ask, always the same:
        1. role     - require_role runs first (a blocked role learns nothing
                      about configuration).
        2. switch   - RAG_ENABLED off -> "disabled", nothing is contacted.
        3. body     - question, plus optional admission_id / patient_id.
        4. ask      - one call through services.rag_client.
        5. log      - grounded answers only, best effort (see below).

  * Only the question text goes to the RAG server. admission_id and patient_id
    are never sent to it; they only go to our own database.

  * The question is NEVER stored or logged (a clinician may type patient
    details into it). ai_summaries gets the answer, its source document names,
    the model name and the ids - nothing the clinician typed.

  * Logging is best effort. Only "answered" replies are logged, and only when
    both ids were supplied. If the database write fails the clinician still
    gets the answer, with "logged": false. Neither the failure text (it can
    contain the database address) nor a traceback is ever returned or logged.

  * The row reuses the existing ai_summaries conventions so the review route
    works unchanged: summary_scope is the caller's role default (doctor
    'clinical', specialist 'consultation', nurse 'care_tasks'), source_reference
    holds the same requested_by_staff_id= stamp ai_summary.py writes, and
    review_status starts 'pending'. A policy row is told apart from a generated
    summary only by a non-empty source_documents.
"""

from flask import Blueprint, current_app, jsonify, request

import settings
from auth import AuthError, get_current_user, require_role
from routes.ai_summary import _DEFAULT_SCOPE_FOR_ROLE, _REQUESTED_BY_PREFIX
from services import database_client as db
from services import rag_client

rag_bp = Blueprint("rag_routes", __name__)

# Longest question accepted (after trimming); matches the RAG server's limit.
MAX_QUESTION_LENGTH = rag_client.MAX_QUESTION_LENGTH


# ============================================================
# Small helpers
# ============================================================
def _reply(status, http_status, code=None, message=None):
    """Uniform JSON error body: a status word plus an error object."""
    body = {"status": status}
    if code is not None:
        body["error"] = {"code": code, "message": message}
    return jsonify(body), http_status


def _bad_request(message):
    return _reply("error", 400, "invalid_request", message)


def _optional_id(body, field):
    """
    Read an optional id from the body.
    Returns (value, None) - value is None when the field is missing or null -
    or (None, error_message) when it is present but not a positive whole number.
    """
    value = body.get(field)
    if value is None:
        return None, None
    # `type(...) is int` rejects true/false, 1.0 and "1" as well.
    if type(value) is not int or value < 1:
        return None, "'{}' must be a positive whole number".format(field)
    return value, None


def _validate_ask_body():
    """
    Check the POST body. Returns ((question, admission_id, patient_id), None)
    when valid, or (None, error_response) when not. Nothing from the body is
    echoed back in an error.
    """
    body = request.get_json(silent=True)  # None if missing or not JSON
    if not isinstance(body, dict):
        return None, _bad_request("request body must be a JSON object")

    if set(body) - {"question", "admission_id", "patient_id"}:
        return None, _bad_request("unsupported field")

    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return None, _bad_request("'question' must be a non-empty string")
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        return None, _bad_request(
            "'question' must be at most {} characters".format(MAX_QUESTION_LENGTH)
        )

    admission_id, problem = _optional_id(body, "admission_id")
    if problem:
        return None, _bad_request(problem)
    patient_id, problem = _optional_id(body, "patient_id")
    if problem:
        return None, _bad_request(problem)

    return (question, admission_id, patient_id), None


def _log_answer(reply, role, user_id, admission_id, patient_id):
    """
    Write one grounded answer to ai_summaries. Returns the new summary_id, or
    None if it was skipped or failed. Never raises, and never records the
    question.
    """
    # Skip: insufficient-context results are not logged, and a row needs both ids.
    if reply["insufficient"] or admission_id is None or patient_id is None:
        return None

    payload = {
        "admission_id": admission_id,
        "patient_id": patient_id,
        "summary_text": reply["answer"],
        "source_documents": list(reply["sources"]),   # real list; the DB API encodes it
        "summary_scope": _DEFAULT_SCOPE_FOR_ROLE[role],
        "review_status": "pending",
        # Same requester stamp as ai_summary.py, so the review route can read it.
        "source_reference": "{}{}".format(_REQUESTED_BY_PREFIX, user_id),
    }
    if reply["model"]:
        payload["model_used"] = reply["model"]

    try:
        created = db.create_ai_summary(payload)
        return created.get("summary_id")
    except Exception:  # noqa: BLE001 - logging must never break the answer
        # Fixed text only: the exception message can hold the database address.
        current_app.logger.warning("could not log a policy answer to ai_summaries")
        return None


# ============================================================
# POST /api/rag/ask
# Body: {"question": "<text>", "admission_id": <int, optional>,
#        "patient_id": <int, optional>}
# ============================================================
@rag_bp.post("/ask")
@require_role("doctor", "nurse", "specialist")   # 1. role (raises AuthError)
def ask():
    # 2. switch - before the body so an "off" service reacts the same to everything.
    if not settings.RAG_ENABLED():
        return _reply("disabled", 503, "rag_disabled", "RAG is not enabled.")

    # 3. body
    checked, error = _validate_ask_body()
    if error:
        return error
    question, admission_id, patient_id = checked

    # 4. ask - only the question text is sent.
    try:
        reply = rag_client.ask(question)
    except rag_client.RagDisabled:
        # Switched off between the check above and the call.
        return _reply("disabled", 503, "rag_disabled", "RAG is not enabled.")
    except rag_client.RagUnavailable:
        return _reply("unavailable", 503, "rag_unavailable",
                      "The policy assistant is temporarily unavailable.")
    except (rag_client.RagBadReply, ValueError):
        # ValueError = our own checks disagreed with the client's; still not a
        # usable answer, and nothing about it is safe or useful to show.
        return _reply("error", 502, "rag_invalid_response",
                      "The policy assistant returned an invalid response.")

    # 5. log (grounded answers only, best effort).
    user = get_current_user() or {}
    summary_id = _log_answer(reply, user.get("role"), user.get("id"),
                             admission_id, patient_id)

    return jsonify({
        "status": "insufficient_context" if reply["insufficient"] else "answered",
        "answer": reply["answer"],
        "sources": reply["sources"],
        "confidence": reply["confidence"],
        "score": reply["score"],
        "logged": summary_id is not None,
        "summary_id": summary_id,
    }), 200


# ============================================================
# GET /api/rag/status
# Always HTTP 200 for a clinical role: the state is the answer, not a failure
# of this route.
# ============================================================
@rag_bp.get("/status")
@require_role("doctor", "nurse", "specialist")
def rag_status():
    info = rag_client.get_status()  # never raises

    if not info.get("enabled"):
        state = "disabled"
    elif info.get("reachable"):
        state = "ok"
    else:
        state = "unavailable"

    return jsonify({"status": state}), 200


# ------------------------------------------------------------
# Blueprint-local error handler - same style as ai_summary.py.
# ------------------------------------------------------------
@rag_bp.errorhandler(AuthError)
def _on_auth_error(err):
    # err.message is fixed text about roles only; it says nothing about config.
    return _reply("error", err.status,
                  "forbidden" if err.status == 403 else "unauthorised", err.message)
