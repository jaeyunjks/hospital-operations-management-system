"""Small, bounded client for the shared Ollama runtime.

Only this module communicates with Ollama. Feature services should load a
versioned prompt and consume the returned :class:`AIResult`; they must supply
their own non-AI fallback when a feature is introduced.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket
import time
from typing import Any
import urllib.error
import urllib.request


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
try:
    OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "90"))
except ValueError:
    OLLAMA_TIMEOUT = 90.0

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
logger = logging.getLogger("student3.ai")
if not logger.handlers:
    terminal_handler = logging.StreamHandler()
    terminal_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(terminal_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@dataclass
class AIResult:
    """A safe result returned for every model call, including failures."""

    ok: bool
    data: Any = None
    error: str | None = None
    outcome: str = "ok"
    prompt_name: str = "inline"
    prompt_version: str = "v1"
    model: str = OLLAMA_MODEL
    duration_seconds: float = 0.0
    retried: bool = False
    raw_response: str | None = None
    eval_count: int | None = None
    eval_duration_ns: int | None = None
    tokens_per_second: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("raw_response", None)
        return result


def load_prompt(name: str, version: str = "v1") -> str:
    """Load a checked-in ``<name>_<version>.md`` prompt, never an inline file."""
    if not name.replace("_", "").isalnum() or not version.replace("_", "").isalnum():
        raise ValueError("Prompt name and version must use letters, numbers, or underscores")
    path = PROMPTS_DIR / f"{name}_{version}.md"
    if not path.is_file():
        raise ValueError(f"Prompt file not found: {path.name}")
    return path.read_text(encoding="utf-8")


def _schema_description(schema: Any) -> str:
    if isinstance(schema, dict):
        return json.dumps({key: getattr(value, "__name__", str(value)) for key, value in schema.items()})
    if isinstance(schema, tuple):
        return " or ".join(getattr(value, "__name__", str(value)) for value in schema)
    return str(schema)


def _validate(value: Any, schema: Any, path: str = "response") -> str | None:
    """Return a human-readable structural validation error, or ``None``."""
    if isinstance(schema, type):
        if not isinstance(value, schema) or (schema is int and isinstance(value, bool)):
            return f"{path} must be {schema.__name__}"
        return None
    if isinstance(schema, tuple) and all(isinstance(option, type) for option in schema):
        if not isinstance(value, schema) or isinstance(value, bool):
            return f"{path} must be one of: {', '.join(option.__name__ for option in schema)}"
        return None
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            return f"{path} must be an object"
        missing = [key for key in schema if key not in value]
        if missing:
            return f"{path} is missing required key(s): {', '.join(missing)}"
        for key, child_schema in schema.items():
            error = _validate(value[key], child_schema, f"{path}.{key}")
            if error:
                return error
        return None
    if isinstance(schema, list):
        if len(schema) != 1:
            return "list schema must contain exactly one item schema"
        if not isinstance(value, list):
            return f"{path} must be an array"
        for index, item in enumerate(value):
            error = _validate(item, schema[0], f"{path}[{index}]")
            if error:
                return error
        return None
    return "Unsupported expected JSON schema"


def _post_generate(prompt: str, timeout: float) -> tuple[dict[str, Any] | None, str | None, str]:
    """Make one Ollama request and classify all transport failures safely."""
    request_data = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_predict": 800},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=request_data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace").strip()
        outcome = "model_unavailable" if error.code == 404 else "unreachable"
        return None, f"Ollama HTTP {error.code}: {detail or error.reason}", outcome
    except (socket.timeout, TimeoutError):
        return None, f"Ollama timed out after {timeout:g} seconds", "timeout"
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        if isinstance(reason, socket.timeout):
            return None, f"Ollama timed out after {timeout:g} seconds", "timeout"
        return None, "Ollama is unreachable", "unreachable"
    except (json.JSONDecodeError, OSError, ValueError) as error:
        return None, f"Ollama response could not be read: {error}", "unreachable"
    return payload, None, "ok"


def _write_log(result: AIResult) -> None:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger.info(
        "AI call | %s | prompt=%s_%s | model=%s | duration=%.3fs | outcome=%s%s | eval_tokens=%s | tokens_per_second=%s",
        timestamp, result.prompt_name, result.prompt_version, result.model,
        result.duration_seconds, result.outcome,
        " | retried" if result.retried else "",
        result.eval_count if result.eval_count is not None else "n/a",
        f"{result.tokens_per_second:.2f}" if result.tokens_per_second is not None else "n/a",
    )


def call_json(prompt: str, expected_schema: Any, *, prompt_name: str = "inline",
              prompt_version: str = "v1", timeout: float | None = None) -> AIResult:
    """Ask Ollama for JSON, validate its shape, and retry malformed output once.

    This function never raises raw transport, parsing, or validation exceptions
    to feature code; inspect ``result.ok`` and ``result.error`` instead.
    """
    started = time.monotonic()
    timeout = OLLAMA_TIMEOUT if timeout is None else timeout
    retry_note = ""
    last_error = ""
    retried = False
    for attempt in range(2):
        request_prompt = prompt + retry_note
        payload, transport_error, outcome = _post_generate(request_prompt, timeout)
        if transport_error:
            result = AIResult(False, error=transport_error, outcome=outcome,
                              prompt_name=prompt_name, prompt_version=prompt_version,
                              duration_seconds=time.monotonic() - started, retried=retried)
            _write_log(result)
            return result
        raw = payload.get("response") if isinstance(payload, dict) else None
        eval_count = payload.get("eval_count") if isinstance(payload.get("eval_count"), int) else None
        eval_duration_ns = payload.get("eval_duration") if isinstance(payload.get("eval_duration"), int) else None
        tokens_per_second = (eval_count / (eval_duration_ns / 1_000_000_000)
                             if eval_count is not None and eval_duration_ns and eval_duration_ns > 0 else None)
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else None
        except json.JSONDecodeError as error:
            parsed = None
            last_error = f"invalid JSON: {error.msg}"
        else:
            last_error = _validate(parsed, expected_schema) or ""
        if not last_error:
            result = AIResult(True, data=parsed, outcome="retried" if retried else "ok",
                              prompt_name=prompt_name, prompt_version=prompt_version,
                              duration_seconds=time.monotonic() - started, retried=retried,
                              raw_response=raw, eval_count=eval_count,
                              eval_duration_ns=eval_duration_ns,
                              tokens_per_second=tokens_per_second)
            _write_log(result)
            return result
        if attempt == 0:
            retried = True
            retry_note = (
                "\n\nYour previous answer was invalid (" + last_error + "). "
                "Return only a JSON value matching this required schema: "
                + _schema_description(expected_schema)
            )
    result = AIResult(False, error=last_error, outcome="invalid_json",
                      prompt_name=prompt_name, prompt_version=prompt_version,
                      duration_seconds=time.monotonic() - started, retried=True,
                      raw_response=raw if isinstance(raw, str) else None,
                      eval_count=eval_count, eval_duration_ns=eval_duration_ns,
                      tokens_per_second=tokens_per_second)
    _write_log(result)
    return result


def run_prompt(name: str, expected_schema: Any, *, version: str = "v1",
               values: dict[str, Any] | None = None) -> AIResult:
    """Load and render a versioned prompt before sending it to Ollama."""
    try:
        prompt = load_prompt(name, version)
        if values:
            prompt = prompt.format(**values)
    except (OSError, KeyError, ValueError) as error:
        result = AIResult(False, error=str(error), outcome="prompt_error",
                          prompt_name=name, prompt_version=version)
        _write_log(result)
        return result
    return call_json(prompt, expected_schema, prompt_name=name, prompt_version=version)


def health_check() -> dict[str, Any]:
    """Report reachability, configured model availability, and a measured probe."""
    report: dict[str, Any] = {
        "ollama_url": OLLAMA_URL,
        "model": OLLAMA_MODEL,
        "reachable": False,
        "model_available": False,
        "round_trip_seconds": None,
        "probe": None,
    }
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=min(OLLAMA_TIMEOUT, 5)) as response:
            tags = json.load(response)
        report["reachable"] = True
        report["model_available"] = any(
            model.get("name") == OLLAMA_MODEL or model.get("model") == OLLAMA_MODEL
            for model in tags.get("models", [])
        )
        if not report["model_available"]:
            report["probe"] = {"ok": False, "error": "Configured model is not available in Ollama"}
            return report
        probe_started = time.monotonic()
        result = run_prompt("smoke", {"ok": bool})
        report["round_trip_seconds"] = round(time.monotonic() - probe_started, 3)
        report["probe"] = result.to_dict()
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, TimeoutError, OSError, ValueError) as error:
        report["probe"] = {"ok": False, "error": "Ollama is unreachable"}
    return report
