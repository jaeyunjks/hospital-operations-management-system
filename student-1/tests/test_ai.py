from unittest.mock import Mock

import pytest

from backend.app import create_app
from backend.services import summary as summary_service
from backend.services.ollama_client import AIUnavailable


def test_summary_passes_notes_to_ollama(monkeypatch):
    summarize_notes = Mock(return_value="Condensed clinical notes")
    monkeypatch.setattr(summary_service.ollama_client, "summarize_notes", summarize_notes)

    result = summary_service.summary("  Patient needs interpreter support.  ")

    assert result == "Condensed clinical notes"
    summarize_notes.assert_called_once()
    assert summarize_notes.call_args.args[0] == "Patient needs interpreter support."


def test_summary_rejects_empty_notes():
    with pytest.raises(ValueError, match="No text provided"):
        summary_service.summary("  ")


def test_emergency_summary_sets_context_flags(monkeypatch):
    monkeypatch.setattr(summary_service, "summary", lambda text: "Emergency summary")

    result = summary_service.summarize_emergency_context(
        "Unidentified patient, possible duplicate, no bed available."
    )

    assert result == {
        "summary": "Emergency summary",
        "flags": {
            "identity_incomplete": True,
            "duplicate_review": True,
            "capacity_issue": True,
        },
    }


def test_ai_endpoint_returns_summary(monkeypatch):
    monkeypatch.setattr(summary_service, "summary", lambda text: "Reviewed notes")
    response = create_app().test_client().post("/api/ai/summary", json={"text": "Notes"})

    assert response.status_code == 200
    assert response.get_json()["data"] == {"summary": "Reviewed notes"}


def test_ai_endpoint_rejects_missing_text():
    response = create_app().test_client().post("/api/ai/summary", json={})

    assert response.status_code == 400
    assert response.get_json()["data"]["error"] == "No text provided"


def test_ai_endpoint_reports_unavailable_service(monkeypatch):
    def unavailable(_text):
        raise AIUnavailable("Ollama is unavailable")

    monkeypatch.setattr(summary_service, "summary", unavailable)
    response = create_app().test_client().post("/api/ai/summary", json={"text": "Notes"})

    assert response.status_code == 503
    assert response.get_json()["data"] == {
        "error": "Ollama is unavailable",
        "summary": "",
    }
