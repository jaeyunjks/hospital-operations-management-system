"""Ollama client for the Student 5 backend/API microservice.

    Backend/API Microservice --HTTP--> Ollama runtime --> open-source LLM

Implemented with the standard library ``urllib``, matching ``database_client``,
so the backend still adds no dependency beyond Flask.

This module knows how to ask a local model for JSON and hand back a parsed
object. It knows nothing about shifts, staff or eligibility — the caller builds
the prompt and validates whatever comes back. That split matters: an LLM reply
is untrusted input, and the module that fetches it is the wrong place to decide
what it is allowed to mean.

EVERY failure path raises ``OllamaError`` carrying a short reason CODE, never a
transport exception and never a stack trace. Callers turn that code into a
fallback; nothing from here should ever reach an API response verbatim.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from config import Config

#: The model could not be reached, refused the request, or took too long.
REASON_UNAVAILABLE = "model_unavailable"

#: The model answered, but not with usable JSON.
REASON_INVALID_OUTPUT = "invalid_model_output"


class OllamaError(Exception):
    """A call to the model failed. ``reason`` is one of the codes above.

    Deliberately NOT an ``ApiError``: a model being down is not an API error.
    The request still succeeds on the deterministic path, so this must never
    escape as an HTTP status.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class OllamaClient:
    """Thin client over the Ollama generate API."""

    def __init__(self, base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 timeout: Optional[float] = None):
        self.base_url = (base_url or Config.OLLAMA_URL).rstrip("/")
        self.model = model or Config.OLLAMA_MODEL
        self.timeout = timeout if timeout is not None else Config.OLLAMA_TIMEOUT

    def generate_json(self, prompt: str, system: Optional[str] = None,
                      model: Optional[str] = None) -> Dict[str, Any]:
        """Ask the model for one JSON object and return it parsed.

        ``format: "json"`` constrains the runtime to emit syntactically valid
        JSON, and ``temperature: 0`` makes the same roster produce the same
        ranking twice — a manager comparing two identical shifts should not see
        the order move. Neither is a guarantee about CONTENT: the object may
        still hold the wrong keys or invented ids, which is the caller's
        problem to validate.

        ``stream: false`` because a ranking is wanted whole or not at all.
        """
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        if system:
            payload["system"] = system

        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            # A 404 here usually means the model name is not pulled. That is an
            # operator problem, not a caller problem, so it still falls back.
            raise OllamaError(REASON_UNAVAILABLE,
                              f"HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise OllamaError(REASON_UNAVAILABLE, str(error.reason)) from error
        except TimeoutError as error:
            # urlopen surfaces a read timeout directly, not wrapped in URLError.
            raise OllamaError(REASON_UNAVAILABLE, "timed out") from error
        except OSError as error:
            # Socket-level failures that escape the two cases above.
            raise OllamaError(REASON_UNAVAILABLE, str(error)) from error

        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as error:
            raise OllamaError(REASON_INVALID_OUTPUT,
                              "envelope was not JSON") from error

        if not isinstance(envelope, dict) or "response" not in envelope:
            raise OllamaError(REASON_INVALID_OUTPUT, "no 'response' field")

        try:
            parsed = json.loads(envelope["response"])
        except (TypeError, json.JSONDecodeError) as error:
            raise OllamaError(REASON_INVALID_OUTPUT,
                              "model output was not JSON") from error

        if not isinstance(parsed, dict):
            raise OllamaError(REASON_INVALID_OUTPUT, "model output was not an object")
        return parsed


#: Shared client instance used by the service layer.
ollama_client = OllamaClient()
