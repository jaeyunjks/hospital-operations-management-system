"""Narrow Student 5 adapter for the shared HOMS RAG HTTP API.

Only ``POST /query`` is available. The feature and retrieval size are fixed in
this module, so callers can supply a bounded question but cannot select another
feature, endpoint, server, or retrieval configuration.
"""

from __future__ import annotations

import json
import math
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict
from urllib.parse import urlsplit

from config import Config

SCHEMA_VERSION = "1.0"
FEATURE = "student-5"
QUERY_PATH = "/query"
TOP_K = 4
MAX_QUESTION_LENGTH = 500
MAX_RESPONSE_BYTES = 64 * 1024

RESPONSE_FIELDS = {
    "schema_version", "status", "question", "feature", "answer",
    "confidence", "citations", "reason", "retrieval", "models",
    "duration_ms",
}
CITATION_FIELDS = {"id", "source", "title", "section", "score", "snippet"}
RETRIEVAL_FIELDS = {"top_score", "threshold", "considered", "relevant"}
MODEL_FIELDS = {"embedding", "generation"}
ANSWERED_CONFIDENCE = {"low", "medium", "high"}
INSUFFICIENT_REASONS = {
    "no_relevant_context", "model_declined", "uncited_answer",
}
ANSWERED_REASONS = {None, "invalid_citations_removed"}
_CITATION_ID = re.compile(r"^S[1-4]$")
_CITATION_GROUP = re.compile(r"\[([^\[\]]{1,40})\]")
_CITATION_IN_GROUP = re.compile(r"\bS[1-4]\b")


class RAGClientError(Exception):
    """Base class for safe, classified adapter failures."""


class RAGDisabledError(RAGClientError):
    pass


class RAGInputError(RAGClientError):
    pass


class RAGUnavailable(RAGClientError):
    pass


class RAGTimeout(RAGClientError):
    pass


class RAGInvalidResponse(RAGClientError):
    pass


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _score(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _cited_identifiers(answer: str) -> set[str]:
    return {
        identifier
        for group in _CITATION_GROUP.findall(answer)
        for identifier in _CITATION_IN_GROUP.findall(group)
    }


def _validated_citations(value: Any) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        raise RAGInvalidResponse()

    identifiers = set()
    for citation in value:
        if not isinstance(citation, dict) or set(citation) != CITATION_FIELDS:
            raise RAGInvalidResponse()
        identifier = citation.get("id")
        source = citation.get("source")
        if (
            not isinstance(identifier, str)
            or not _CITATION_ID.fullmatch(identifier)
            or identifier in identifiers
            or not _nonblank(source)
            or not (source.startswith(f"{FEATURE}/") or source.startswith("shared/"))
            or not _nonblank(citation.get("title"))
            or not _nonblank(citation.get("section"))
            or not _score(citation.get("score"))
            or not _nonblank(citation.get("snippet"))
        ):
            raise RAGInvalidResponse()
        identifiers.add(identifier)
    return value


def _validate_metadata(body: Dict[str, Any]) -> None:
    retrieval = body.get("retrieval")
    if not isinstance(retrieval, dict) or set(retrieval) != RETRIEVAL_FIELDS:
        raise RAGInvalidResponse()
    top_score = retrieval.get("top_score")
    considered = retrieval.get("considered")
    relevant = retrieval.get("relevant")
    if (
        (top_score is not None and not _score(top_score))
        or not _score(retrieval.get("threshold"))
        or isinstance(considered, bool)
        or not isinstance(considered, int)
        or not 0 <= considered <= TOP_K
        or isinstance(relevant, bool)
        or not isinstance(relevant, int)
        or not 0 <= relevant <= considered
    ):
        raise RAGInvalidResponse()

    models = body.get("models")
    if (
        not isinstance(models, dict)
        or set(models) != MODEL_FIELDS
        or not all(_nonblank(models.get(field)) for field in MODEL_FIELDS)
        or not _nonnegative_number(body.get("duration_ms"))
    ):
        raise RAGInvalidResponse()


def _validated_response(body: Any, question: str) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) != RESPONSE_FIELDS:
        raise RAGInvalidResponse()
    if (
        body.get("schema_version") != SCHEMA_VERSION
        or body.get("question") != question
        or body.get("feature") != FEATURE
        or not _nonblank(body.get("answer"))
    ):
        raise RAGInvalidResponse()

    _validate_metadata(body)
    citations = _validated_citations(body.get("citations"))
    status = body.get("status")
    confidence = body.get("confidence")
    reason = body.get("reason")

    if status == "answered":
        cited = _cited_identifiers(body["answer"])
        if (
            confidence not in ANSWERED_CONFIDENCE
            or not citations
            or reason not in ANSWERED_REASONS
            or any(citation["id"] not in cited for citation in citations)
        ):
            raise RAGInvalidResponse()
    elif status == "insufficient_context":
        if confidence != "none" or citations or reason not in INSUFFICIENT_REASONS:
            raise RAGInvalidResponse()
    else:
        raise RAGInvalidResponse()

    # Expose only the stable documentation-guidance fields Student 5 needs.
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "answer": body["answer"],
        "confidence": confidence,
        "citations": citations,
        "reason": reason,
    }


def _validated_question(question: Any) -> str:
    if not isinstance(question, str) or not question.strip():
        raise RAGInputError()
    if len(question) > MAX_QUESTION_LENGTH:
        raise RAGInputError()
    return question.strip()


class StaffShiftRAGClient:
    """Request-scoped adapter exposing one bounded documentation query."""

    def __init__(self, enabled: bool = Config.RAG_ENABLED,
                 server_url: str = Config.RAG_SERVER_URL,
                 timeout: float = Config.RAG_TIMEOUT):
        self.enabled = enabled
        self.server_url = server_url
        self.timeout = timeout

    def ask(self, question: str) -> Dict[str, Any]:
        if not self.enabled:
            raise RAGDisabledError()

        question = _validated_question(question)
        parsed = urlsplit(self.server_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise RAGInvalidResponse()

        payload = {
            "question": question,
            "feature": FEATURE,
            "top_k": TOP_K,
        }
        request = urllib.request.Request(
            self.server_url.rstrip("/") + QUERY_PATH,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code == 504:
                raise RAGTimeout() from error
            if error.code in {502, 503}:
                raise RAGUnavailable() from error
            raise RAGInvalidResponse() from error
        except (socket.timeout, TimeoutError) as error:
            raise RAGTimeout() from error
        except urllib.error.URLError as error:
            if isinstance(getattr(error, "reason", None), (socket.timeout, TimeoutError)):
                raise RAGTimeout() from error
            raise RAGUnavailable() from error
        except OSError as error:
            raise RAGUnavailable() from error

        if len(raw) > MAX_RESPONSE_BYTES:
            raise RAGInvalidResponse()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RAGInvalidResponse() from error
        return _validated_response(body, question)
