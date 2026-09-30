"""
rag_client.py - asks the shared RAG server a policy question.

What it does:
  * ask(question) sends POST /query to the shared RAG server, limited to
    Student 2's own documents (feature "student-2", the same name as the
    server's knowledge/student-2 folder; the server also searches "shared").
  * Returns ONE small dict and nothing else from the server's reply:
        {answer, sources, confidence, score, insufficient, model}
  * Extra fields in the reply are ignored; missing essential ones are an error.
    No retries.
  * get_status() checks the server's /health for the UI.

ERROR-HANDLING CONTRACT (like mcp_client.py, NOT ollama_client.py):
  * ask RAISES a classified error instead of returning a fallback string, so
    the caller can tell "RAG is off" from "RAG is down" from "RAG sent junk".
  * Every message is fixed text - no traceback, server address or reply body
    ever appears in one, and the original exception is not chained.
  * The on/off switch, URL and timeout come from settings.py and are read on
    every call.
"""

import math

import requests

import settings

# Which feature's documents the server should search (see FEATURES in rag.py).
FEATURE = "student-2"
QUERY_PATH = "/query"
HEALTH_PATH = "/health"
# Longest a status check may wait (seconds); asking a question may take longer.
STATUS_TIMEOUT = 5
# Same limit the server enforces on questions.
MAX_QUESTION_LENGTH = 500

# The only two statuses the server sends, and the confidence words each allows.
STATUS_ANSWERED = "answered"
STATUS_INSUFFICIENT = "insufficient_context"
ANSWERED_CONFIDENCE = ("low", "medium", "high")
INSUFFICIENT_CONFIDENCE = "none"


# ------------------------------------------------------------
# Errors
# ------------------------------------------------------------
class RagError(Exception):
    """Base class, so a caller can catch every RAG failure at once."""


class RagDisabled(RagError):
    """RAG_ENABLED is off."""


class RagUnavailable(RagError):
    """The server is unreachable, timed out, or reported a server-side failure."""


class RagBadReply(RagError):
    """The server answered, but the reply is missing or breaks essential fields."""


_BAD_REPLY = "RAG reply is missing expected parts."


# ------------------------------------------------------------
# Reply checking
# ------------------------------------------------------------
def _is_number(value):
    """A real, finite number (bool is not a number here)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _read_reply(body):
    """Pick the five fields we expose out of a reply; ignore everything else."""
    if not isinstance(body, dict):
        raise RagBadReply(_BAD_REPLY)

    status = body.get("status")
    answer = body.get("answer")
    confidence = body.get("confidence")
    citations = body.get("citations")
    retrieval = body.get("retrieval")

    # Essential fields: status, answer, confidence, citations, retrieval.
    if status not in (STATUS_ANSWERED, STATUS_INSUFFICIENT):
        raise RagBadReply(_BAD_REPLY)
    if not isinstance(answer, str) or not answer.strip():
        raise RagBadReply(_BAD_REPLY)
    if not isinstance(citations, list) or not isinstance(retrieval, dict):
        raise RagBadReply(_BAD_REPLY)

    # Document names come from each citation's "source"; keep first-seen order.
    sources = []
    for citation in citations:
        source = citation.get("source") if isinstance(citation, dict) else None
        if not isinstance(source, str) or not source.strip():
            raise RagBadReply(_BAD_REPLY)
        if source not in sources:
            sources.append(source)

    # top_score is null only when nothing was ranked at all.
    score = retrieval.get("top_score")
    if score is None and status == STATUS_INSUFFICIENT:
        score = 0.0
    if not _is_number(score):
        raise RagBadReply(_BAD_REPLY)

    # An answer must be grounded and carry a low/medium/high confidence;
    # a refusal must carry "none" and cite nothing.
    if status == STATUS_ANSWERED:
        if confidence not in ANSWERED_CONFIDENCE or not sources:
            raise RagBadReply(_BAD_REPLY)
    elif confidence != INSUFFICIENT_CONFIDENCE or sources:
        raise RagBadReply(_BAD_REPLY)

    # The model that wrote the answer (optional: None if the server omits it).
    models = body.get("models")
    model = models.get("generation") if isinstance(models, dict) else None
    if not isinstance(model, str) or not model.strip():
        model = None

    return {
        "answer": answer,
        "sources": sources,
        "confidence": confidence,
        "score": float(score),
        "insufficient": status == STATUS_INSUFFICIENT,
        "model": model,
    }


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------
def ask(question):
    """
    Ask the RAG server a policy question about Student 2's features.

    Returns:
      {"answer": str, "sources": [document names], "confidence": str,
       "score": float, "insufficient": bool, "model": str or None}

    Raises:
      RagDisabled    - RAG_ENABLED is off (nothing is contacted).
      RagUnavailable - server unreachable, timed out, or answered 5xx.
      RagBadReply    - the reply is unreadable or missing essential fields
                       (also any non-5xx error status, e.g. a rejected request).
      ValueError     - question is blank, not a string, or over 500 characters
                       (a caller bug).
    """
    # Read settings fresh on every call so tests can flip them mid-run.
    if not settings.RAG_ENABLED():
        raise RagDisabled("RAG integration is disabled.")

    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question must be a non-empty string.")
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(f"Question must be at most {MAX_QUESTION_LENGTH} characters.")

    try:
        response = requests.post(
            settings.RAG_SERVER_URL() + QUERY_PATH,
            json={"question": question, "feature": FEATURE},
            timeout=settings.RAG_TIMEOUT(),  # explicit - never hang the route
            allow_redirects=False,
        )
    except requests.RequestException:
        # Timeout, refused connection, DNS failure, invalid URL, etc.
        # `from None` drops the chained cause so the address never leaks.
        raise RagUnavailable("RAG server is unreachable or timed out.") from None

    # 5xx (model down, index missing, gateway timeout) = server side problem.
    if response.status_code >= 500:
        raise RagUnavailable("RAG server is unreachable or timed out.")
    # Any other non-2xx (rejected request, redirect, ...) is not a usable answer.
    if not response.ok:
        raise RagBadReply(_BAD_REPLY)

    try:
        body = response.json()
    except ValueError:
        raise RagBadReply(_BAD_REPLY) from None

    return _read_reply(body)


def get_status():
    """
    Report RAG state for the UI. Never raises.

    Returns {"enabled": bool, "reachable": bool}. The server is only contacted
    when RAG is enabled; "reachable" means GET /health answered 200 "ok" (index
    built and models installed), so a degraded server counts as not reachable.
    """
    status = {"enabled": settings.RAG_ENABLED(), "reachable": False}
    if not status["enabled"]:
        return status

    try:
        response = requests.get(
            settings.RAG_SERVER_URL() + HEALTH_PATH,
            timeout=min(settings.RAG_TIMEOUT(), STATUS_TIMEOUT),  # short probe
            allow_redirects=False,
        )
        body = response.json() if response.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return status

    status["reachable"] = isinstance(body, dict) and body.get("status") == "ok"
    return status
