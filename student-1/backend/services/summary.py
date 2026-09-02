# AI-assisted patient administration notes summary
# Creation date: 31/08/2026
#
# Plan -> Act -> Observe -> Adapt as implemented by this feature:
#

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

def summary(text):
    """Return a summary for patient administration notes using the Ollama service."""
    return ollama_client.summarize_notes(str(text or '').strip(), fallback=None)