"""Client for the local Ollama runtime and an approved open-source LLM.

Release 0 requires the flow
    Frontend -> Backend/API -> Ollama -> approved LLM -> Backend -> Frontend

Two rules govern every call:

1. A timeout is always set. A local model can stall for tens of seconds,
   and a hung request would freeze the coordinator's screen during the
   showcase.
2. The caller always has a rule-based fallback. AI output is advisory,
   so the feature must keep working when the model is slow, absent or
   returns malformed output.
"""

import json
import re

import requests

import config


class AIUnavailable(Exception):
    """The model could not be reached or returned unusable output."""


def generate(prompt, system=None, temperature=0.2):
    """Send one prompt to Ollama and return the raw text response."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system:
        payload["system"] = system

    try:
        response = requests.post(
            config.OLLAMA_URL + "/api/generate",
            json=payload,
            timeout=config.OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise AIUnavailable("Ollama request failed: {}".format(error)) from error

    text = response.json().get("response", "").strip()
    if not text:
        raise AIUnavailable("Ollama returned an empty response")
    return text


def generate_json(prompt, system=None, retries=None):
    """Ask for JSON and return the parsed object.

    Small local models frequently wrap JSON in prose or code fences, so
    the first object or array in the reply is extracted and parsed. A
    failed parse is retried; the retry itself is the Observe -> Adapt
    step of the agentic loop and is reported back to the caller.
    """
    attempts = config.OLLAMA_RETRIES if retries is None else retries
    errors = []

    for attempt in range(1, attempts + 1):
        text = generate(prompt, system=system, temperature=0.1)
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1)), attempt
            except json.JSONDecodeError as error:
                errors.append("attempt {}: {}".format(attempt, error))
        else:
            errors.append("attempt {}: no JSON found".format(attempt))

    raise AIUnavailable("Model did not return valid JSON — " + "; ".join(errors))


def is_available():
    """Cheap reachability probe used by /health."""
    try:
        response = requests.get(config.OLLAMA_URL + "/api/tags", timeout=3)
        return response.status_code == 200
    except requests.RequestException:
        return False
