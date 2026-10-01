"""Bounded client for the shared, non-containerised HOMS RAG server.

Only this module talks to RAG. Every question is scoped to the Room & Bed
knowledge base (``feature: student-4``, plus the shared documents), and only
an allowlisted, validated subset of the server's response is returned. A
citation from another feature's documents is treated as an invalid response
rather than displayed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import os
import socket
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

FEATURE = "student-4"
MAX_QUESTION_LENGTH = 500
STATUSES = ("answered", "insufficient_context")
CONFIDENCE = {"answered": ("high", "medium", "low"), "insufficient_context": ("none",)}

RAG_ENABLED = os.environ.get("RAG_ENABLED", "false").strip().lower() == "true"
RAG_SERVER_URL = os.environ.get("RAG_SERVER_URL", "http://127.0.0.1:8100").strip().rstrip("/")
try:
    RAG_TIMEOUT = float(os.environ.get("RAG_TIMEOUT", "100"))
except ValueError:
    RAG_TIMEOUT = 100.0

logger = logging.getLogger("student4.rag")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


class InvalidResponse(ValueError):
    """The RAG server answered, but not with the documented contract."""


@dataclass
class RAGResult:
    """A safe result for every question, including transport failures.

    ``outcome`` is ``answered``, ``insufficient_context``, ``disabled``,
    ``timeout``, ``unavailable`` or ``rag_error``.
    """

    ok: bool
    outcome: str
    question: str
    answer: str | None = None
    confidence: str | None = None
    citations: list = field(default_factory=list)
    reason: str | None = None
    retrieval: dict | None = None
    models: dict | None = None
    error: str | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _request(method: str, path: str, payload: dict | None = None, timeout: float | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(
        RAG_SERVER_URL + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(call, timeout=timeout or RAG_TIMEOUT) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except (json.JSONDecodeError, AttributeError):
            return exc.code, {}


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidResponse("missing {}".format(name))
    return value


def _citation(item: Any) -> dict:
    if not isinstance(item, dict):
        raise InvalidResponse("citation is not an object")
    score = item.get("score")
    if type(score) not in (int, float):
        raise InvalidResponse("citation score is not a number")
    source = _text(item.get("source"), "citation source")
    if not source.startswith((FEATURE + "/", "shared/")):
        raise InvalidResponse("citation is outside the room and bed knowledge base")
    return {"id": _text(item.get("id"), "citation id"), "source": source,
            "title": _text(item.get("title"), "citation title"),
            "section": _text(item.get("section"), "citation section"),
            "score": score,
            "snippet": item.get("snippet") if isinstance(item.get("snippet"), str) else ""}


def _project(body: Any, question: str) -> RAGResult:
    """Validate the documented /query contract and keep only display fields."""

    if not isinstance(body, dict) or body.get("status") not in STATUSES:
        raise InvalidResponse("unknown status")
    status = body["status"]
    if body.get("confidence") not in CONFIDENCE[status]:
        raise InvalidResponse("confidence does not match status")
    citations = body.get("citations")
    if not isinstance(citations, list):
        raise InvalidResponse("citations are missing")
    citations = [_citation(item) for item in citations]
    if (status == "answered") != bool(citations):
        raise InvalidResponse("answered responses need citations and refusals must have none")
    retrieval = body.get("retrieval") if isinstance(body.get("retrieval"), dict) else {}
    return RAGResult(
        ok=True, outcome=status, question=question,
        answer=_text(body.get("answer"), "answer"), confidence=body["confidence"],
        citations=citations,
        reason=body.get("reason") if isinstance(body.get("reason"), str) else None,
        retrieval={key: retrieval.get(key)
                   for key in ("top_score", "threshold", "considered", "relevant")},
        models=body.get("models") if isinstance(body.get("models"), dict) else None,
    )


def ask(question: str) -> RAGResult:
    """Ask one room and bed question; callers validate the question first."""

    if not RAG_ENABLED:
        return RAGResult(False, "disabled", question, error="RAG mode is disabled")
    started = time.perf_counter()
    try:
        status, body = _request("POST", "/query", {"question": question, "feature": FEATURE})
        if status == 200:
            result = _project(body, question)
        else:
            detail = body.get("error", {}) if isinstance(body, dict) else {}
            code = detail.get("code", "http_{}".format(status)) if isinstance(detail, dict) \
                else "http_{}".format(status)
            result = RAGResult(False, "timeout" if status == 504 else "rag_error", question,
                               error="Shared RAG server could not answer ({})".format(code))
    except (socket.timeout, TimeoutError):
        result = RAGResult(False, "timeout", question, error="Shared RAG server timed out")
    except urllib.error.URLError as exc:
        timed_out = isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError))
        result = RAGResult(False, "timeout" if timed_out else "unavailable", question,
                           error="Shared RAG server timed out" if timed_out
                           else "Shared RAG server is unavailable")
    except (json.JSONDecodeError, InvalidResponse, OSError):
        result = RAGResult(False, "unavailable", question,
                           error="Shared RAG server returned an invalid response")
    result.duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info("[RAG] ask outcome=%s confidence=%s citations=%s duration_ms=%s server=%s",
                result.outcome, result.confidence or "-", len(result.citations),
                result.duration_ms, RAG_SERVER_URL)
    return result


def status() -> dict:
    """Non-crashing status for the UI and CI; only contacts RAG when enabled."""

    base = {"enabled": RAG_ENABLED, "server_url": RAG_SERVER_URL, "feature": FEATURE}
    if not RAG_ENABLED:
        return {**base, "reachable": False, "ready": False, "documents": []}
    try:
        health_code, _health = _request("GET", "/health", timeout=5)
        _code, sources = _request(
            "GET", "/sources?" + urllib.parse.urlencode({"feature": FEATURE}), timeout=5)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {**base, "reachable": False, "ready": False, "documents": [],
                "error": "Shared RAG server is unavailable"}
    documents = sources.get("documents", []) if isinstance(sources, dict) else []
    return {**base, "reachable": True, "ready": health_code == 200 and bool(documents),
            "documents": [{"title": d.get("title"), "source": d.get("source")}
                          for d in documents if isinstance(d, dict)]}
