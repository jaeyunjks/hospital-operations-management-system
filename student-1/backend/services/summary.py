# AI-assisted patient administration notes summary
# Creation date: 31/08/2026
#
# Plan -> Act -> Observe -> Adapt as implemented by this feature:
#   Plan     receive the patient administration notes and prepare them for summarization
#   Act      send the notes to Ollama, which generates a summary
#   Observe  validate the model's reply against the original notes; record whether the coordinator accepted or overrode it
#   Adapt    every later summary is recomputed against current notes, so the next answer reflects the most recent information

from __future__ import annotations

import re
from typing import Any

try:
    from backend.services import ollama_client
except ImportError:  # pragma: no cover - supports local execution
    from services import ollama_client

SYSTEM_PROMPT = (
    "You are a helpful assistant that summarizes patient administration notes. "
    "You will receive a string of text containing patient administration notes. "
    "Your task is to generate a concise summary of the notes, highlighting key information such as "
    "common dates for appointments, patient concerns, family history, and any other relevant details for "
    "reception and administrative staff. "
)

# Deterministic fallback used when the model is unavailable or the input is sparse.
def _fallback_summary(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return "No patient administration notes were provided."

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    if not sentences:
        return cleaned[:500]

    first = sentences[:3]
    summary_bits = []
    for sentence in first:
        summary_bits.append(sentence)

    summary = " ".join(summary_bits)
    if len(summary) > 500:
        summary = summary[:497].rstrip() + "..."
    return summary

# Return a summary for patient administration notes using the Ollama service.
def summary(text: str | None) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValueError("No text provided for summarization.")
    return ollama_client.summarize_notes(cleaned, fallback=_fallback_summary(cleaned))

# Create a compact emergency-specific summary with status flags for the care team.
def summarize_emergency_context(text: str | None) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValueError("No emergency context provided.")

    emergency_summary = summary(cleaned)
    lower = cleaned.lower()
    flags = {
        "identity_incomplete": any(token in lower for token in ("unknown", "unidentified", "name not provided", "no id", "no mrn")),
        "duplicate_review": any(token in lower for token in ("possible duplicate", "same name", "alias", "duplicate")),
        "capacity_issue": any(token in lower for token in ("full", "no bed", "no capacity", "overflow", "high acuity")),
    }
    return {
        "summary": emergency_summary,
        "flags": flags,
    }


__all__ = [
    "SYSTEM_PROMPT",
    "summary",
    "summarize_emergency_context",
    "_fallback_summary",
]