"""Coverage narration tests for the Student 5 backend/API microservice.

Phase B adds an OPTIONAL narration layer over authoritative coverage figures:

    coverage_service -> facts -> explicit request -> Ollama -> manager reads

The deterministic figures are the answer; the narrative is commentary beside
them. These tests mostly prove what the model cannot do to those figures — it
cannot replace them, contradict them, or slip a number past them — and that a
missing or broken model costs the paragraph and nothing else.

The coverage arithmetic itself is proved in test_backend_coverage.py.
"""

from __future__ import annotations

import json

import pytest

from config import Config
from prompts import coverage_summary as coverage_prompt
from services import ai_service
from services.ollama_client import (REASON_INVALID_OUTPUT, REASON_UNAVAILABLE,
                                    OllamaError)


class FakeOllama:
    """Stands in for OllamaClient, recording what it was asked."""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def generate_json(self, prompt, system=None, model=None):
        self.calls.append({"prompt": prompt, "system": system})
        if self.error is not None:
            raise self.error
        return self.reply

    @property
    def prompt(self):
        assert self.calls, "the model was never called"
        return self.calls[0]["prompt"]


# Narratives used below avoid digits AND number words unless a test is about
# them, so an incidental figure never decides an unrelated assertion.
NARRATIVE = ("Emergency and Surgery both carry shortages. Surgery has nobody "
             "assigned at all, so it is the more pressing of the pair.")
PRIORITIES = ["Surgery shift has nobody assigned",
              "Emergency night cover still short"]


def _reply(summary=NARRATIVE, priorities=None):
    return {coverage_prompt.SUMMARY_KEY: summary,
            coverage_prompt.PRIORITIES_KEY:
                PRIORITIES if priorities is None else priorities}


@pytest.fixture
def ai_on(monkeypatch):
    """Switch AI-Mode on and install a fake client; returns the fake."""
    def _install(reply=None, error=None):
        fake = FakeOllama(reply=reply if reply is not None else _reply(),
                          error=error)
        monkeypatch.setattr(Config, "AI_ENABLED", True)
        monkeypatch.setattr(ai_service, "ollama_client", fake)
        return fake
    return _install


def _summarise(client, narrate=True, **body):
    response = client.post("/api/shifts/coverage-summary",
                           json={"narrate": narrate, **body})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


# ----------------------------------------------------------- happy path
class TestSuccessfulNarration:
    def test_narrative_is_returned(self, client, ai_on):
        ai_on()
        body = _summarise(client)
        assert body["narrative"] == NARRATIVE

    def test_priorities_are_returned_in_order(self, client, ai_on):
        ai_on()
        assert _summarise(client)["priorities"] == PRIORITIES

    def test_provenance_reports_ai(self, client, ai_on):
        ai_on()
        body = _summarise(client)
        assert body["mode"] == "ai"
        assert body["generation"]["source"] == "ollama"
        assert body["generation"]["fallback_reason"] is None
        assert body["generation"]["model"] == Config.OLLAMA_MODEL

    def test_deterministic_figures_are_still_authoritative(self, client, ai_on):
        """The narrative sits beside the numbers, never in place of them."""
        ai_on()
        body = _summarise(client)
        assert body["headline"]
        assert body["summary"]["total_shortfall"] == 2
        assert body["summary"]["coverage_pct"] == 33
        assert len(body["gaps"]) == 2

    def test_narrative_is_flattened_and_capped(self, client, ai_on):
        ai_on(_reply(summary="Emergency\n\tis   short " + "x" * 900))
        narrative = _summarise(client)["narrative"]
        assert "\n" not in narrative and "   " not in narrative
        assert len(narrative) <= 600

    def test_priorities_are_capped_in_number_and_length(self, client, ai_on):
        ai_on(_reply(priorities=["Surgery short " + "y" * 400] * 9))
        priorities = _summarise(client)["priorities"]
        assert len(priorities) <= 5
        assert all(len(item) <= 120 for item in priorities)

    def test_empty_priorities_are_allowed(self, client, ai_on):
        ai_on(_reply(priorities=[]))
        body = _summarise(client)
        assert body["mode"] == "ai"
        assert body["priorities"] == []

    def test_narration_creates_no_assignment(self, client, ai_on):
        """Summarising is a read. Nothing in this path touches the roster."""
        ai_on()
        before = client.get("/api/shifts/1/assignments").get_json()["count"]
        _summarise(client)
        after = client.get("/api/shifts/1/assignments").get_json()["count"]
        assert before == after == 1


# ------------------------------------------------------ the LLM payload
class TestPromptPayload:
    def test_facts_carry_the_coverage_vocabulary(self, client, ai_on):
        fake = ai_on()
        _summarise(client)
        for field in ("required_positions", "assigned_positions",
                      "filled_positions", "shortfall", "surplus",
                      "coverage_status", "coverage_pct"):
            assert field in fake.prompt, field

    def test_internal_identifiers_are_not_sent(self, client, ai_on):
        """The narrative names departments and times, not primary keys."""
        fake = ai_on()
        _summarise(client)
        assert "shift_id" not in fake.prompt
        assert "staff_id" not in fake.prompt

    def test_staff_names_are_never_sent(self, client, ai_on, stub_database):
        fake = ai_on()
        _summarise(client)
        for name in ("Amara Okafor", "Daniel Reyes", "Mei Lin Tan"):
            assert name not in fake.prompt

    def test_free_text_is_never_sent(self, client, ai_on, stub_database):
        """Notes and absence reasons carry clinical and personal detail."""
        stub_database.staff[1]["notes"] = "Needs regular breaks"
        stub_database.shifts[1]["notes"] = "Cover for bereavement"
        stub_database.requests[2]["reason"] = "Oncology appointment"
        stub_database.requests[2]["notes"] = "Consultant referral attached"
        fake = ai_on()
        _summarise(client)
        for leak in ("Needs regular breaks", "bereavement",
                     "Oncology appointment", "Consultant referral"):
            assert leak not in fake.prompt

    def test_projected_shift_fields_are_exactly_the_allowed_set(self):
        projected = ai_service._coverage_fact_shift({
            "shift_id": 1, "department": "Emergency", "shift_date": "2026-08-24",
            "start_time": "07:00", "end_time": "15:00",
            "required_role": "Registered Nurse", "required_staff_count": 2,
            "assigned_staff_count": 1, "filled_staff_count": 1,
            "shortfall": 1, "surplus": 0, "coverage_status": "Understaffed",
            "notes": "private",
        })
        assert set(projected) == {
            "department", "shift_date", "start_time", "end_time",
            "required_role", "required_positions", "assigned_positions",
            "filled_positions", "shortfall", "surplus", "coverage_status"}

    def test_a_truncated_roster_still_reports_its_true_size(self, client,
                                                            ai_on, stub_database):
        """A capped list must not read as the whole picture."""
        for index in range(30):
            stub_database.create_shift(
                department="Emergency", shift_date="2026-08-24",
                start_time="07:00", end_time="15:00",
                required_role="Registered Nurse", required_staff_count=1)
        fake = ai_on()
        body = _summarise(client)
        facts = json.loads(fake.prompt.split("\n\n")[1])
        assert facts["shifts_described"] == 20
        assert facts["shifts_total"] == 32
        assert body["summary"]["total_shifts"] == 32

    def test_the_system_prompt_forbids_inventing_figures(self):
        system = coverage_prompt.SYSTEM_PROMPT.lower()
        assert "use only the numbers you are given" in system
        assert "never calculate a new number" in system
        assert "occupancy" in system
        assert "do not recommend actions" in system


# ------------------------------------------------- number safety
class TestUnsupportedNumbersRejected:
    def test_invented_figure_is_rejected(self, client, ai_on):
        """A fluent summary with a wrong number is the dangerous failure."""
        ai_on(_reply(summary="Emergency is short by 47 nurses this week."))
        body = _summarise(client)
        assert body["mode"] == "rule-based"
        assert body["generation"]["fallback_reason"] == \
            ai_service.FALLBACK_UNSUPPORTED_NUMBERS
        assert body["narrative"] is None

    def test_invented_percentage_is_rejected(self, client, ai_on):
        ai_on(_reply(summary="Overall coverage sits at 91% across the roster."))
        assert _summarise(client)["mode"] == "rule-based"

    def test_invented_figure_in_a_priority_is_rejected(self, client, ai_on):
        ai_on(_reply(priorities=["Surgery needs 88 more staff"]))
        assert _summarise(client)["mode"] == "rule-based"

    def test_number_words_are_checked_too(self, client, ai_on):
        """'eleven nurses short' must not pass where '11' would not."""
        ai_on(_reply(summary="Emergency is short by eleven nurses."))
        assert _summarise(client)["mode"] == "rule-based"

    def test_a_supported_figure_is_allowed(self, client, ai_on):
        """33% and 2 shifts are both in the facts, so both may be quoted."""
        ai_on(_reply(summary="Coverage is 33% across 2 shifts.",
                     priorities=[]))
        body = _summarise(client)
        assert body["mode"] == "ai"
        assert body["narrative"] == "Coverage is 33% across 2 shifts."

    def test_a_supported_number_word_is_allowed(self, client, ai_on):
        ai_on(_reply(summary="Both shifts are short.", priorities=[]))
        assert _summarise(client)["mode"] == "ai"

    def test_supported_numbers_include_dates_and_times(self):
        supported = ai_service._numbers_in(
            {"shift_date": "2026-08-24", "start_time": "07:00"})
        assert {2026, 8, 24, 7, 0} <= supported

    def test_claimed_numbers_read_digits_and_words(self):
        assert ai_service._numbers_claimed("3 short, four spare") == {3, 4}

    def test_rejection_names_no_internal_detail(self, client, ai_on):
        ai_on(_reply(summary="Emergency is short by 47 nurses."))
        note = _summarise(client)["note"]
        assert "47" not in note
        assert "Traceback" not in note and "OllamaError" not in note


# -------------------------------------------------------------- fallback
class TestFallback:
    def _assert_deterministic(self, body, reason):
        assert body["mode"] == "rule-based"
        assert body["generation"]["source"] == "deterministic"
        assert body["generation"]["fallback_reason"] == reason
        assert body["narrative"] is None
        assert body["priorities"] == []
        # The authoritative figures survive every fallback.
        assert body["headline"]
        assert body["summary"]["total_shifts"] == 2
        assert body["summary"]["coverage_pct"] == 33

    def test_malformed_model_output_falls_back(self, client, ai_on):
        ai_on(error=OllamaError(REASON_INVALID_OUTPUT, "not JSON"))
        self._assert_deterministic(_summarise(client), REASON_INVALID_OUTPUT)

    def test_missing_summary_key_falls_back(self, client, ai_on):
        ai_on({"something_else": "text"})
        self._assert_deterministic(_summarise(client), REASON_INVALID_OUTPUT)

    def test_blank_summary_falls_back(self, client, ai_on):
        ai_on(_reply(summary="   "))
        self._assert_deterministic(_summarise(client), REASON_INVALID_OUTPUT)

    def test_priorities_that_are_not_a_list_falls_back(self, client, ai_on):
        ai_on(_reply(priorities="Surgery first"))
        self._assert_deterministic(_summarise(client), REASON_INVALID_OUTPUT)

    def test_timeout_falls_back(self, client, ai_on):
        ai_on(error=OllamaError(REASON_UNAVAILABLE, "timed out"))
        self._assert_deterministic(_summarise(client), REASON_UNAVAILABLE)

    def test_connection_failure_falls_back(self, client, ai_on):
        ai_on(error=OllamaError(REASON_UNAVAILABLE, "Connection refused"))
        self._assert_deterministic(_summarise(client), REASON_UNAVAILABLE)

    def test_ai_disabled_does_not_call_the_model(self, client, monkeypatch):
        fake = FakeOllama(_reply())
        monkeypatch.setattr(Config, "AI_ENABLED", False)
        monkeypatch.setattr(ai_service, "ollama_client", fake)
        body = _summarise(client)
        assert fake.calls == []
        self._assert_deterministic(body, ai_service.FALLBACK_AI_DISABLED)

    def test_narration_not_requested_does_not_call_the_model(self, client, ai_on):
        """The default path — every page load takes this branch."""
        fake = ai_on()
        body = _summarise(client, narrate=False)
        assert fake.calls == []
        self._assert_deterministic(body, ai_service.FALLBACK_NOT_REQUESTED)

    def test_omitting_narrate_entirely_does_not_call_the_model(self, client, ai_on):
        fake = ai_on()
        body = client.post("/api/shifts/coverage-summary", json={}).get_json()
        assert fake.calls == []
        assert body["generation"]["fallback_reason"] == \
            ai_service.FALLBACK_NOT_REQUESTED

    def test_no_matching_shifts_does_not_call_the_model(self, client, ai_on):
        fake = ai_on()
        body = _summarise(client, shift_date="2030-01-01")
        assert fake.calls == []
        assert body["mode"] == "rule-based"
        assert body["generation"]["fallback_reason"] == ai_service.FALLBACK_NO_SHIFTS
        assert body["summary"]["total_shifts"] == 0
        assert body["summary"]["coverage_pct"] is None

    def test_fallback_notes_never_leak_internals(self, client, ai_on):
        ai_on(error=OllamaError(REASON_UNAVAILABLE, "refused by 127.0.0.1"))
        note = _summarise(client)["note"]
        assert "could not be reached" in note
        for leak in ("Traceback", "127.0.0.1", "refused", "OllamaError",
                     "urllib", "Exception"):
            assert leak not in note

    def test_every_fallback_reason_has_a_note(self):
        for reason in (ai_service.FALLBACK_NOT_REQUESTED,
                       ai_service.FALLBACK_AI_DISABLED,
                       ai_service.FALLBACK_NO_SHIFTS,
                       ai_service.FALLBACK_UNSUPPORTED_NUMBERS,
                       REASON_UNAVAILABLE, REASON_INVALID_OUTPUT):
            assert ai_service._COVERAGE_FALLBACK_NOTES[reason].strip()

    def test_a_broken_model_never_becomes_an_http_error(self, client, ai_on):
        ai_on(error=OllamaError(REASON_UNAVAILABLE, "down"))
        response = client.post("/api/shifts/coverage-summary",
                               json={"narrate": True})
        assert response.status_code == 200


# ------------------------------------------------------------- the route
class TestEndpointContract:
    def test_narrate_must_be_a_boolean(self, client):
        response = client.post("/api/shifts/coverage-summary",
                               json={"narrate": "yes"})
        assert response.status_code == 400

    def test_still_requires_the_manager_role(self, client):
        response = client.post("/api/shifts/coverage-summary",
                               json={"narrate": True},
                               headers={"X-HOMS-Role": "Employee",
                                        "X-HOMS-Staff-Id": "2"})
        assert response.status_code == 403

    def test_department_filter_still_applies(self, client, ai_on):
        ai_on()
        body = _summarise(client, department="Surgery")
        assert body["summary"]["total_shifts"] == 1

    def test_context_still_advertises_the_task_and_model(self, client):
        body = _summarise(client, narrate=False)
        assert body["context"]["task"] == "summarise_staffing_coverage"
        assert "model" in body["context"]
