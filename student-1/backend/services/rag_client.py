"""Bounded Student-1 adapter for the shared HOMS RAG server."""

from __future__ import annotations

import json
import math
import re
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = "1.0"
FEATURE = "student-1"
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
CONFIDENCE_LEVELS = {"low", "medium", "high"}
INSUFFICIENT_REASONS = {
	"no_relevant_context", "model_declined", "uncited_answer",
}
ANSWERED_REASONS = {None, "invalid_citations_removed"}
_CITATION_GROUP = re.compile(r"\[([^\[\]]{1,40})\]")
_CITATION_ID = re.compile(r"\bS[1-4]\b")


class RAGClientError(Exception):
	"""Base class for classified adapter failures."""


class RAGDisabled(RAGClientError):
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


def _cited_ids(answer: str) -> set[str]:
	return {
		identifier
		for group in _CITATION_GROUP.findall(answer)
		for identifier in _CITATION_ID.findall(group)
	}


def _validated_response(body: Any, question: str) -> dict[str, Any]:
	if not isinstance(body, dict) or set(body) != RESPONSE_FIELDS:
		raise RAGInvalidResponse()
	if (
		body.get("schema_version") != SCHEMA_VERSION
		or body.get("question") != question
		or body.get("feature") != FEATURE
		or not _nonblank(body.get("answer"))
	):
		raise RAGInvalidResponse()

	retrieval = body.get("retrieval")
	if not isinstance(retrieval, dict) or set(retrieval) != RETRIEVAL_FIELDS:
		raise RAGInvalidResponse()
	considered = retrieval.get("considered")
	relevant = retrieval.get("relevant")
	if (
		(retrieval.get("top_score") is not None and not _score(retrieval["top_score"]))
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
	duration = body.get("duration_ms")
	if (
		not isinstance(models, dict)
		or set(models) != MODEL_FIELDS
		or not all(_nonblank(models.get(field)) for field in MODEL_FIELDS)
		or isinstance(duration, bool)
		or not isinstance(duration, (int, float))
		or not math.isfinite(duration)
		or duration < 0
	):
		raise RAGInvalidResponse()

	citations = body.get("citations")
	if not isinstance(citations, list):
		raise RAGInvalidResponse()
	identifiers = set()
	for citation in citations:
		if not isinstance(citation, dict) or set(citation) != CITATION_FIELDS:
			raise RAGInvalidResponse()
		identifier = citation.get("id")
		source = citation.get("source")
		if (
			not isinstance(identifier, str)
			or not re.fullmatch(r"S[1-4]", identifier)
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

	status = body.get("status")
	confidence = body.get("confidence")
	reason = body.get("reason")
	if status == "answered":
		if (
			confidence not in CONFIDENCE_LEVELS
			or not citations
			or reason not in ANSWERED_REASONS
			or _cited_ids(body["answer"]) != identifiers
		):
			raise RAGInvalidResponse()
	elif status == "insufficient_context":
		if confidence != "none" or citations or reason not in INSUFFICIENT_REASONS:
			raise RAGInvalidResponse()
	else:
		raise RAGInvalidResponse()

	return {
		"schema_version": SCHEMA_VERSION,
		"status": status,
		"answer": body["answer"],
		"confidence": confidence,
		"citations": citations,
		"reason": reason,
	}


class PatientAdmissionRAGClient:
	"""Request-scoped adapter limited to Student-1 documentation queries."""

	def __init__(self, enabled: bool, server_url: str, timeout: float):
		self.enabled = enabled
		self.server_url = server_url
		self.timeout = timeout

	def ask(self, question: Any) -> dict[str, Any]:
		if not self.enabled:
			raise RAGDisabled()
		if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_LENGTH:
			raise RAGInputError()
		question = question.strip()

		try:
			parsed = urlsplit(self.server_url)
			valid_url = (
				parsed.scheme in {"http", "https"}
				and parsed.hostname
				and parsed.username is None
				and parsed.password is None
				and parsed.path in {"", "/"}
				and not parsed.query
				and not parsed.fragment
			)
		except ValueError:
			valid_url = False
		if (
			not valid_url
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
		call = urllib.request.Request(
			self.server_url.rstrip("/") + QUERY_PATH,
			data=json.dumps(payload).encode("utf-8"),
			headers={"Content-Type": "application/json", "Accept": "application/json"},
			method="POST",
		)
		try:
			with urllib.request.urlopen(call, timeout=self.timeout) as response:
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
