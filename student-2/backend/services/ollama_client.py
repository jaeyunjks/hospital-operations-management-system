"""
ollama_client.py - wraps calls to the shared Ollama container that runs an
open-source LLM (Llama 3.1 8B) for the AI summary feature.

What it does:
  * Takes the plain text of ONE admission's clinical records / consultation
    reason / care task list (the caller decides which, based on the requesting
    user's role) and asks the model for a short summary.
  * Builds a prompt that tells the model to summarise ONLY the supplied text
    and to invent nothing that is not present in the input.
  * Adjusts the wording by `summary_scope` so a diagnosis + care plan, a
    consultation reason, and a task list get the right tone and never blend.

ERROR-HANDLING CONTRACT (like external_services.py, NOT database_client.py):
  * summarise_clinical_history NEVER raises.
  * On a timeout, connection failure, bad status, or unparseable body it
    returns FALLBACK_SUMMARY - a plain string the calling route can store or
    display as-is. Every workflow stays usable when the AI service is down;
    the AI summary is an optional extra, never a hard dependency.
"""

import os

import requests

# ------------------------------------------------------------
# Config - env-overridable for Docker, sensible localhost defaults.
# The default model is pinned to the one approved for this feature.
# ------------------------------------------------------------
OLLAMA_BASE_URL = os.environ.get(
    "OLLAMA_BASE_URL", "http://localhost:11434"
).rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

# Explicit per-request timeout (connect + read), in seconds. Kept short: the
# route must not hang waiting on the LLM. NOT the requests default of None.
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "8"))

# Returned instead of a summary whenever the model cannot be reached or used.
FALLBACK_SUMMARY = "No AI summary could be generated (summary service unavailable)."

# Valid scopes and the scope-specific prompt wording. Each entry describes what
# the input text IS and the tone the summary should take.
_SCOPE_WORDING = {
    "clinical": (
        "The text below is a patient's clinical records for a single admission - "
        "diagnoses and care plan. Summarise the current diagnosis and the planned "
        "care in a concise, clinical tone suitable for a doctor or specialist."
    ),
    "consultation": (
        "The text below is the stated reason for a consultation request on a "
        "single admission. Summarise why the consultation was requested in one or "
        "two plain sentences. Do not speculate about diagnosis or treatment."
    ),
    "care_tasks": (
        "The text below is a list of nursing care tasks for a single admission. "
        "Summarise what care is outstanding and what has been done, as a short "
        "practical handover note for a nurse."
    ),
}

# Fall back to the clinical wording if an unknown scope is passed.
_DEFAULT_SCOPE = "clinical"


def _build_prompt(records_text, summary_scope):
    """Assemble the full prompt string sent to the model."""
    # Pick scope-specific framing; unknown scopes degrade to clinical.
    scope_line = _SCOPE_WORDING.get(summary_scope, _SCOPE_WORDING[_DEFAULT_SCOPE])

    # Guard-rails: summarise only, invent nothing.
    return (
        "You are a clinical summarisation assistant for a hospital system.\n"
        f"{scope_line}\n\n"
        "Rules:\n"
        "- Summarise ONLY the information contained in the text below.\n"
        "- Do NOT add, infer, or invent any clinical detail that is not "
        "explicitly present in the text.\n"
        "- If the text is empty or has too little detail to summarise, say so "
        "plainly instead of guessing.\n"
        "- Keep the summary brief (a few sentences at most).\n\n"
        "TEXT TO SUMMARISE:\n"
        f"{records_text}\n"
    )


def summarise_clinical_history(records_text, summary_scope):
    """
    Summarise one admission's text via the Ollama LLM.

    Args:
      records_text:  plain text of the clinical records / consultation reason /
                     care task list for a single admission.
      summary_scope: 'clinical', 'consultation', or 'care_tasks' - adjusts the
                     prompt wording and tone. Unknown values are treated as
                     'clinical'.

    Returns:
      The summary text on success, or FALLBACK_SUMMARY (a plain string, never
      an exception) if the model times out, is unreachable, errors, or returns
      an empty / unparseable body.
    """
    # Nothing to summarise - don't bother calling the model.
    if not records_text or not str(records_text).strip():
        return FALLBACK_SUMMARY

    prompt = _build_prompt(str(records_text), summary_scope)

    # Ollama's non-streaming generate endpoint returns the whole reply at once.
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=OLLAMA_TIMEOUT,  # explicit - never hang the route
        )
    except requests.RequestException:
        # Timeout, connection refused, DNS failure, etc. - service unavailable.
        return FALLBACK_SUMMARY

    # Any non-2xx (model not pulled, server error, ...) -> fallback.
    if not response.ok:
        return FALLBACK_SUMMARY

    # Body should be JSON with a "response" field holding the generated text.
    try:
        body = response.json()
    except ValueError:
        return FALLBACK_SUMMARY

    summary = (body.get("response") or "").strip()
    if not summary:
        return FALLBACK_SUMMARY

    return summary
