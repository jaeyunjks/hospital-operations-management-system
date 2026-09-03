"""AI behaviour: usefulness, constraints, failure handling, oversight.

The model is never running during CI, so these tests pin down what the
feature does when the AI is absent or returns nonsense — which is
exactly the behaviour the showcase depends on.
"""

import pytest

from conftest import data


@pytest.fixture()
def ai(monkeypatch):
    """Control what the Ollama client returns for a single test."""
    from services import ollama_client

    class Control:
        def unavailable(self):
            def boom(*_a, **_k):
                raise ollama_client.AIUnavailable("Ollama not running")
            monkeypatch.setattr(ollama_client, "generate", boom)
            monkeypatch.setattr(ollama_client, "generate_json", boom)

        def returns_json(self, payload):
            monkeypatch.setattr(ollama_client, "generate_json",
                                lambda *a, **k: (payload, 1))

        def returns_text(self, text):
            monkeypatch.setattr(ollama_client, "generate", lambda *a, **k: text)

    return Control()


def suggest(api, requirements="Ventilator support and continuous monitoring"):
    return data(api.post("/api/rooms/suggest",
                         json={"patient_requirements": requirements}))


def test_requirements_are_classified_into_a_care_category(api, ai):
    ai.unavailable()
    assert suggest(api)["plan"]["care_category"] == "Short-term"
    assert suggest(api, "Elective surgery, general anaesthetic")["plan"]["care_category"] == "Surgical"
    assert suggest(api, "Extended rehabilitation programme")["plan"]["care_category"] == "Long-term"


def test_suggestions_stay_inside_the_requested_category(api, ai):
    ai.unavailable()
    result = suggest(api, "Elective surgery, post-operative recovery")
    rows = data(api.get("/api/rooms/availability?care_category=Surgical&bed_status=available"))
    allowed = {r["bed_id"] for r in rows}
    assert result["suggestions"]
    assert all(s["bed_id"] in allowed for s in result["suggestions"])


def test_falls_back_to_rules_when_the_model_is_unavailable(api, ai):
    ai.unavailable()
    result = suggest(api)
    assert result["source"] == "fallback"
    assert result["suggestions"], "the coordinator must still see options"


def test_hallucinated_beds_are_discarded(api, ai):
    """A bed the model invented must never reach the coordinator."""
    ai.returns_json([{"bed_id": 999, "reason": "Invented bed"}])
    result = suggest(api)
    assert 999 not in [s["bed_id"] for s in result["suggestions"]]
    assert result["source"] == "fallback"


def test_partially_valid_output_keeps_only_real_beds(api, ai):
    rows = data(api.get("/api/rooms/availability?care_category=Short-term&bed_status=available"))
    real = rows[0]["bed_id"]
    ai.returns_json([{"bed_id": real, "reason": "Monitored bed"},
                     {"bed_id": 4242, "reason": "Not a real bed"}])
    result = suggest(api)
    assert [s["bed_id"] for s in result["suggestions"]] == [real]
    assert 4242 in result["discarded_ids"]


def test_output_is_always_labelled_advisory(api, ai):
    ai.unavailable()
    assert suggest(api)["advisory"] is True


def test_ai_never_changes_bed_state(api, ai):
    """AI ranks; only an authorised employee allocates."""
    before = data(api.get("/api/beds"))
    ai.returns_json([{"bed_id": 16, "reason": "Best match"}])
    suggest(api)
    assert data(api.get("/api/beds")) == before


def test_no_available_bed_directs_the_user_to_a_shortage_case(api, ai):
    ai.unavailable()
    # Fill every free Long-term bed so the category has no candidates.
    for row in data(api.get("/api/rooms/availability?care_category=Long-term&bed_status=available")):
        api.post("/api/arrangements", json={
            "bed_id": row["bed_id"], "patient_id": 8000 + row["bed_id"],
            "purpose": "Inpatient stay", "start_time": "2026-09-01 08:00",
            "status": "In Progress",
        })
    result = suggest(api, "Extended rehabilitation programme, stable")
    assert result["suggestions"] == []
    assert "shortage case" in result["message"]


def test_occupancy_summary_falls_back_to_real_numbers(api, ai):
    ai.unavailable()
    result = data(api.post("/api/rooms/occupancy-summary", json={}))
    assert result["source"] == "fallback"
    assert "beds available" in result["summary"]
    assert result["stats"]["by_care_category"]


def test_occupancy_summary_uses_the_model_when_available(api, ai):
    ai.returns_text("Critical Care is under pressure. Two theatres are usable.")
    result = data(api.post("/api/rooms/occupancy-summary", json={}))
    assert result["source"] == "ai"
    assert result["advisory"] is True
