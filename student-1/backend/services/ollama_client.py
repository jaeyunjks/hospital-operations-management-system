# Client for the local Ollama LLM microservice.
# Creation date: 31/08/2026

# Release 0 requires the flow
#     Frontend -> Backend/API -> Ollama -> approved LLM -> Backend -> Frontend

# Two rules govern every call:
# 1. A timeout is always set. A local model can stall for tens of seconds,
#    and a hung request would freeze the coordinator's screen during the
#    showcase.
# 2. The caller always has a rule-based fallback. AI output is advisory,
#    so the feature must keep working when the model is slow, absent or
#    returns malformed output.

import json
import re

import requests

try:
    from backend.config import OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_RETRIES, OLLAMA_URL
except ImportError:  # pragma: no cover - supports direct module execution
    import config

    OLLAMA_MODEL = config.OLLAMA_MODEL
    OLLAMA_TIMEOUT = config.OLLAMA_TIMEOUT
    OLLAMA_RETRIES = config.OLLAMA_RETRIES
    OLLAMA_URL = config.OLLAMA_URL


# Raised when the Ollama LLM microservice is unavailable.
class AIUnavailable(Exception):
    pass


def _generate_url(base_url):
    base = str(base_url).rstrip("/")
    if base.endswith("/api/generate"):
        return base
    return f"{base}/api/generate"


def _extract_response_text(data):
    if not isinstance(data, dict):
        return ""

    if "response" in data and isinstance(data["response"], str):
        return data["response"].strip()
    if "result" in data and isinstance(data["result"], str):
        return data["result"].strip()
    if "message" in data and isinstance(data["message"], str):
        return data["message"].strip()
    return ""


def _rule_based_fallback(prompt, system=None):
    text = str(prompt or "").strip()
    if not text:
        return "No text provided for AI analysis."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return text

    summary_parts = []
    for sentence in sentences[:3]:
        summary_parts.append(sentence)

    return " ".join(summary_parts)


def generate(prompt, system=None, temperature=0.2, retries=None):
    if prompt is None or str(prompt).strip() == "":
        raise ValueError("Prompt cannot be empty")

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": str(prompt).strip(),
        "stream": False,
        "options": {
            "temperature": float(temperature),
        },
    }
    if system:
        payload["system"] = str(system)

    attempts = max(1, int(retries if retries is not None else OLLAMA_RETRIES))
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(
                _generate_url(OLLAMA_URL),
                json=payload,
                timeout=OLLAMA_TIMEOUT,
            )
            try:
                data = response.json()
            except ValueError as exc:
                raise ValueError(f"Malformed JSON response from Ollama: {response.text[:200]}") from exc

            if response.status_code >= 400:
                error_text = data.get("error") if isinstance(data, dict) else None
                raise ValueError(error_text or response.text or "Ollama request failed")

            text = _extract_response_text(data)
            if not text:
                raise ValueError("Ollama response missing generated text")
            return text

        except (requests.exceptions.RequestException, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= attempts:
                break

    raise AIUnavailable(f"Ollama service unavailable: {last_error}") from last_error

# Return model output when available, otherwise provide a deterministic local fallback.
def generate_with_fallback(prompt, system=None, temperature=0.2, retries=None, fallback=None):
    try:
        return generate(prompt, system=system, temperature=temperature, retries=retries)
    except AIUnavailable:
        if fallback is not None:
            return fallback
        return _rule_based_fallback(prompt, system=system)

# Convenience method for patient-note summarisation flows used by Student-1 services.
def summarize_notes(text, fallback=None):
    system_prompt = (
        "You are an assistant that summarises patient administration notes. "
        "Highlight key dates, patient concerns, family information, follow-up needs, "
        "and any important administrative action items in clear, concise language."
    )

    try:
        return generate(text, system=system_prompt, temperature=0.2)
    except AIUnavailable:
        if fallback is not None:
            return fallback
        return _rule_based_fallback(text, system=system_prompt)

# Check whether the Ollama server is reachable and can accept requests.
def healthcheck():
    try:
        response = requests.get(f"{str(OLLAMA_URL).rstrip('/')}/api/tags", timeout=OLLAMA_TIMEOUT)
        if response.status_code >= 400:
            return {"ok": False, "error": response.text}
        payload = response.json()
        return {"ok": True, "models": payload.get("models", [])}
    except (requests.exceptions.RequestException, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

# Return the model list from the local Ollama installation
def list_models():
    try:
        response = requests.get(f"{str(OLLAMA_URL).rstrip('/')}/api/tags", timeout=OLLAMA_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return data.get("models", [])
    except (requests.exceptions.RequestException, ValueError) as exc:
        raise AIUnavailable(f"Unable to list Ollama models: {exc}") from exc


__all__ = [
    "AIUnavailable",
    "generate",
    "generate_with_fallback",
    "summarize_notes",
    "healthcheck",
    "list_models",
    "_rule_based_fallback",
]