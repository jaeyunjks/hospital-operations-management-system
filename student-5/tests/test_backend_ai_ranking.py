"""Ollama ranking tests for the Student 5 backend/API microservice.

Phase 2 adds an OPTIONAL ranking layer on top of deterministic eligibility:

    eligibility_service -> eligible only -> optional Ollama -> manager assigns

These tests exist mostly to prove what the model CANNOT do. It cannot add a
candidate, resurrect an ineligible one, duplicate anyone, drop anyone by
staying silent, or take the roster down with it when it is slow or broken.
Eligibility itself is proved in test_backend_eligibility.py and is not
re-tested here.

No test in this file talks to a real model. ``FakeOllama`` stands in for the
client and records exactly what it was asked, which is also how the payload
privacy tests read the prompt.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from config import Config
from prompts import suggest_staff as suggest_staff_prompt
from services import ai_service
from services.ollama_client import (REASON_INVALID_OUTPUT, REASON_UNAVAILABLE,
                                    OllamaError)

# Shift 2 in the stub is a Surgery shift on 2026-08-25 requiring a Doctor.
SHIFT = 2

# Deterministic order for that shift once all three doctors are eligible:
# same-department staff first (Surgery before Emergency), then by name.
DETERMINISTIC = [3, 4, 2]


def _add_weekly_match(stub_database, staff_id):
    """Make the doctor available for the Tuesday 08:00-16:00 test shift."""
    row_id = stub_database._next_weekly_id
    stub_database._next_weekly_id += 1
    stub_database.weekly[row_id] = {
        "availability_id": row_id, "staff_id": staff_id, "day_of_week": 1,
        "start_time": "08:00", "end_time": "16:00", "notes": None,
    }


class FakeOllama:
    """Stands in for OllamaClient, recording what it was asked."""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def generate_json(self, prompt, system=None, model=None):
        self.calls.append({"prompt": prompt, "system": system, "model": model})
        if self.error is not None:
            raise self.error
        return self.reply

    @property
    def prompt(self):
        assert self.calls, "the model was never called"
        return self.calls[0]["prompt"]


@pytest.fixture
def three_doctors(stub_database):
    """Give shift 2 three eligible doctors, so ordering is observable.

    Staff 3 is On Leave in the base fixture; freeing them and adding a fourth
    record produces a shortlist long enough for a reordering to mean something.
    """
    stub_database.staff[3]["availability_status"] = "Available"
    stub_database.staff[4] = {
        "staff_id": 4, "name": "Ravi Chandran", "role": "Doctor",
        "department": "Surgery", "specialisation": "Orthopaedics",
        "availability_status": "Available", "employment_status": "Part-Time",
        "notes": None,
    }
    for staff_id in (2, 3, 4):
        _add_weekly_match(stub_database, staff_id)
    return stub_database


@pytest.fixture
def ai_on(monkeypatch):
    """Switch AI-Mode on and install a fake client; returns the fake."""
    def _install(reply=None, error=None):
        fake = FakeOllama(reply=reply, error=error)
        monkeypatch.setattr(Config, "AI_ENABLED", True)
        monkeypatch.setattr(ai_service, "ollama_client", fake)
        return fake
    return _install


def _ranking(*entries):
    """Build a model reply in the documented shape."""
    return {suggest_staff_prompt.RANKING_KEY: list(entries)}


def _entry(staff_id, rationale="Suits this ward"):
    return {"staff_id": staff_id, "rationale": rationale}


def _suggest(client, shift_id=SHIFT, **body):
    return client.post("/api/shifts/suggest-staff",
                       json={"shift_id": shift_id, **body}).get_json()


# --------------------------------------------------------- happy path
class TestSuccessfulRanking:
    def test_model_order_is_applied(self, client, three_doctors, ai_on):
        ai_on(_ranking(_entry(2), _entry(4), _entry(3)))
        body = _suggest(client)
        assert [row["staff_id"] for row in body["suggestions"]] == [4, 3, 2]

    def test_mode_and_ranking_envelope_report_ai(self, client, three_doctors, ai_on):
        ai_on(_ranking(_entry(2), _entry(4), _entry(3)))
        body = _suggest(client)
        assert body["mode"] == "ai"
        assert body["ranking"]["source"] == "ollama"
        assert body["ranking"]["fallback_reason"] is None
        assert body["ranking"]["model"] == Config.OLLAMA_MODEL

    def test_rationales_are_returned(self, client, three_doctors, ai_on):
        ai_on(_ranking(_entry(2, "Knows the ward"), _entry(4, "Orthopaedic cover"),
                       _entry(3, "Anaesthetics on site")))
        body = _suggest(client)
        rationales = [row["rationale"] for row in body["suggestions"]]
        assert "Same department" in rationales[0]
        assert "Cross-department" in rationales[2]
        assert all("rostered hours this week" in text for text in rationales)
        assert all(model_text not in " ".join(rationales) for model_text in (
            "Knows the ward", "Orthopaedic cover", "Anaesthetics on site"))

    def test_rationale_is_flattened_and_capped(self, client, three_doctors, ai_on):
        """Model prose is ignored; the fact-composed explanation stays bounded."""
        ai_on(_ranking(_entry(2, "Knows\n\tthe   ward " + "x" * 300),
                       _entry(4), _entry(3)))
        rationale = _suggest(client)["suggestions"][0]["rationale"]
        assert "\n" not in rationale and "\t" not in rationale
        assert "   " not in rationale
        assert len(rationale) <= 120
        assert "x" * 20 not in rationale

    def test_a_candidate_without_a_rationale_is_still_ranked(
            self, client, three_doctors, ai_on):
        ai_on(_ranking({"staff_id": 2}, _entry(4), _entry(3)))
        body = _suggest(client)
        assert [row["staff_id"] for row in body["suggestions"]] == [4, 3, 2]
        assert "Cross-department" in body["suggestions"][2]["rationale"]

    def test_ranking_never_creates_an_assignment(self, client, three_doctors, ai_on):
        """Suggesting is not assigning, whatever the model recommends."""
        ai_on(_ranking(_entry(2), _entry(4), _entry(3)))
        before = client.get(f"/api/shifts/{SHIFT}/assignments").get_json()["count"]
        _suggest(client)
        after = client.get(f"/api/shifts/{SHIFT}/assignments").get_json()["count"]
        assert before == after == 0


class TestShortlistSelection:
    def test_primary_shortlist_is_capped_and_remaining_staff_are_alternatives(
            self, client, three_doctors, ai_on):
        three_doctors.staff[5] = {
            "staff_id": 5, "name": "Zoe Walsh", "role": "Doctor",
            "department": "Surgery", "specialisation": "General Surgery",
            "availability_status": "Available", "employment_status": "Full-Time",
            "notes": None,
        }
        _add_weekly_match(three_doctors, 5)
        fake = ai_on(_ranking(_entry(3), _entry(4), _entry(5)))

        body = _suggest(client, limit=5)

        assert [row["staff_id"] for row in body["suggestions"]] == [3, 4, 5]
        assert [row["staff_id"] for row in body["alternatives"]] == [2]
        assert len(body["suggestions"]) == 3
        assert '"staff_id": 2' in fake.prompt
        assert "eligible_alternatives" in fake.prompt

    def test_outside_weekly_availability_is_an_assignable_alternative(
            self, client, stub_database, ai_on):
        fake = ai_on(_ranking(_entry(2)))

        body = _suggest(client)

        assert fake.calls == []
        assert body["suggestions"] == []
        assert [row["staff_id"] for row in body["alternatives"]] == [2]
        assert body["alternatives"][0]["eligible"] is True
        assert body["ranking"]["fallback_reason"] == \
            ai_service.FALLBACK_NO_PRIMARY_CANDIDATES
        assert "not caused by a lack of candidates" in body["assessment"]

    def test_lower_real_rostered_hours_win_within_same_department(
            self, client, three_doctors, ai_on):
        three_doctors.shifts[3] = {
            "shift_id": 3, "department": "Surgery",
            "shift_date": "2026-08-24", "start_time": "07:00",
            "end_time": "17:00", "required_role": "Doctor",
            "required_staff_count": 1, "shift_status": "Planned", "notes": None,
        }
        three_doctors.assignments[2] = {
            "assignment_id": 2, "shift_id": 3, "staff_id": 3,
            "assignment_status": "Assigned", "approved_by": None,
            "approved_at": None,
        }
        ai_on(_ranking(_entry(3), _entry(4), _entry(2)))

        body = _suggest(client)

        assert [row["staff_id"] for row in body["suggestions"]] == [4, 3, 2]
        assert body["suggestions"][0]["weekly_rostered_hours"] == 0
        assert body["suggestions"][1]["weekly_rostered_hours"] == 10


# ------------------------------------------------- defensive validation
class TestModelOutputIsNotTrusted:
    def test_hallucinated_staff_id_is_ignored(self, client, three_doctors, ai_on):
        ai_on(_ranking(_entry(999), _entry(2), _entry(4), _entry(3)))
        ids = [row["staff_id"] for row in _suggest(client)["suggestions"]]
        assert 999 not in ids
        assert ids == [4, 3, 2]

    def test_an_ineligible_candidate_cannot_be_promoted_by_the_model(
            self, client, three_doctors, ai_on):
        """The model naming a blocked staff member must change nothing."""
        three_doctors.staff[3]["availability_status"] = "On Leave"
        ai_on(_ranking(_entry(3), _entry(2), _entry(4)))
        ids = [row["staff_id"] for row in _suggest(client)["suggestions"]]
        assert 3 not in ids
        assert ids == [4, 2]

    def test_duplicate_staff_id_is_taken_once(self, client, three_doctors, ai_on):
        ai_on(_ranking(_entry(2), _entry(2), _entry(4), _entry(3)))
        ids = [row["staff_id"] for row in _suggest(client)["suggestions"]]
        assert ids == [4, 3, 2]
        assert len(ids) == len(set(ids))

    def test_omitted_candidate_is_appended_in_deterministic_order(
            self, client, three_doctors, ai_on):
        """Silence cannot drop anyone: the rest keep their existing order."""
        ai_on(_ranking(_entry(2)))
        ids = [row["staff_id"] for row in _suggest(client)["suggestions"]]
        assert ids == [3, 4, 2]

    def test_appended_candidates_carry_only_fact_composed_rationales(
            self, client, three_doctors, ai_on):
        ai_on(_ranking(_entry(2, "Knows the ward")))
        suggestions = _suggest(client)["suggestions"]
        assert all("rationale" in row for row in suggestions)
        assert all("Knows the ward" not in row["rationale"] for row in suggestions)

    def test_every_eligible_candidate_survives_a_mangled_ranking(
            self, client, three_doctors, ai_on):
        ai_on(_ranking(_entry(999), _entry(2), None, "nonsense",
                       {"rationale": "no id"}, _entry(2)))
        ids = {row["staff_id"] for row in _suggest(client)["suggestions"]}
        assert ids == {2, 3, 4}

    def test_string_and_bare_integer_ids_are_accepted(
            self, client, three_doctors, ai_on):
        """Tolerate a smaller model's shorthand rather than lose the ranking."""
        ai_on(_ranking("2", 4, _entry(3)))
        ids = [row["staff_id"] for row in _suggest(client)["suggestions"]]
        assert ids == [4, 3, 2]

    def test_boolean_is_not_read_as_staff_one(self, client, three_doctors, ai_on):
        ai_on(_ranking(True, _entry(2), _entry(4), _entry(3)))
        ids = [row["staff_id"] for row in _suggest(client)["suggestions"]]
        assert ids == [4, 3, 2]


# ------------------------------------------------------------- fallback
class TestFallback:
    def _assert_deterministic(self, body, reason):
        assert body["mode"] == "rule-based"
        assert body["ranking"]["source"] == "deterministic"
        assert body["ranking"]["fallback_reason"] == reason
        assert [row["staff_id"] for row in body["suggestions"]] == DETERMINISTIC
        assert all("rostered hours this week" in row["rationale"]
                   for row in body["suggestions"])

    def test_malformed_model_json_falls_back(self, client, three_doctors, ai_on):
        ai_on(error=OllamaError(REASON_INVALID_OUTPUT, "model output was not JSON"))
        self._assert_deterministic(_suggest(client), REASON_INVALID_OUTPUT)

    def test_missing_ranking_key_falls_back(self, client, three_doctors, ai_on):
        ai_on({"something_else": []})
        self._assert_deterministic(_suggest(client), REASON_INVALID_OUTPUT)

    def test_model_only_needs_to_return_the_ranking(
            self, client, three_doctors, ai_on):
        ai_on({suggest_staff_prompt.RANKING_KEY:
               [_entry(2), _entry(4), _entry(3)]})
        body = _suggest(client)
        assert body["mode"] == "ai"
        assert body["assessment"].startswith("Eligible staff are identified")

    def test_ranking_that_is_not_a_list_falls_back(self, client, three_doctors, ai_on):
        ai_on({suggest_staff_prompt.RANKING_KEY: "first pick 2"})
        self._assert_deterministic(_suggest(client), REASON_INVALID_OUTPUT)

    def test_ranking_of_only_unknown_ids_falls_back(self, client, three_doctors, ai_on):
        """Nothing usable came back, so the order must not claim to be AI's."""
        ai_on(_ranking(_entry(999), _entry(1000)))
        self._assert_deterministic(_suggest(client), REASON_INVALID_OUTPUT)

    def test_timeout_falls_back(self, client, three_doctors, ai_on):
        ai_on(error=OllamaError(REASON_UNAVAILABLE, "timed out"))
        self._assert_deterministic(_suggest(client), REASON_UNAVAILABLE)

    def test_connection_failure_falls_back(self, client, three_doctors, ai_on):
        ai_on(error=OllamaError(REASON_UNAVAILABLE, "Connection refused"))
        self._assert_deterministic(_suggest(client), REASON_UNAVAILABLE)

    def test_ai_disabled_falls_back_without_calling_the_model(
            self, client, three_doctors, monkeypatch):
        fake = FakeOllama(_ranking(_entry(2)))
        monkeypatch.setattr(Config, "AI_ENABLED", False)
        monkeypatch.setattr(ai_service, "ollama_client", fake)
        body = _suggest(client)
        assert fake.calls == []
        self._assert_deterministic(body, ai_service.FALLBACK_AI_DISABLED)
        assert body["ai_enabled"] is False

    def test_empty_eligible_list_does_not_call_the_model(
            self, client, three_doctors, ai_on):
        """Shift 1's only qualified nurse is already on it: nothing to rank."""
        fake = ai_on(_ranking(_entry(1)))
        body = _suggest(client, shift_id=1)
        assert fake.calls == []
        assert body["suggestions"] == []
        assert body["mode"] == "rule-based"
        assert body["ranking"]["fallback_reason"] == ai_service.FALLBACK_NO_CANDIDATES

    def test_fallback_note_explains_without_leaking_internals(
            self, client, three_doctors, ai_on):
        ai_on(error=OllamaError(REASON_UNAVAILABLE, "Connection refused by 127.0.0.1"))
        note = _suggest(client)["note"]
        assert "could not be reached" in note
        for leak in ("Traceback", "127.0.0.1", "Connection refused",
                     "OllamaError", "urllib", "Exception"):
            assert leak not in note

    def test_every_fallback_reason_has_a_note(self):
        """A new reason code must not fall through as a blank explanation."""
        for reason in (ai_service.FALLBACK_AI_DISABLED,
                       ai_service.FALLBACK_NO_CANDIDATES,
                       ai_service.FALLBACK_NO_PRIMARY_CANDIDATES,
                       ai_service.FALLBACK_UNSUPPORTED_NUMBERS,
                       ai_service.FALLBACK_UNSUPPORTED_POLICY,
                       REASON_UNAVAILABLE, REASON_INVALID_OUTPUT):
            assert ai_service._FALLBACK_NOTES[reason].strip()


# ---------------------------------------------------- payload minimisation
class TestPromptPrivacy:
    def test_candidates_are_identified_only_by_staff_id(
            self, client, three_doctors, ai_on):
        fake = ai_on(_ranking(_entry(2), _entry(4), _entry(3)))
        _suggest(client)
        for name in ("Daniel Reyes", "Mei Lin Tan", "Ravi Chandran"):
            assert name not in fake.prompt
        assert '"staff_id": 2' in fake.prompt

    def test_staff_notes_are_never_sent(self, client, three_doctors, ai_on):
        three_doctors.staff[2]["notes"] = "Needs regular breaks after surgery"
        fake = ai_on(_ranking(_entry(2), _entry(4), _entry(3)))
        _suggest(client)
        assert "Needs regular breaks" not in fake.prompt
        assert "notes" not in fake.prompt

    def test_unavailability_reasons_are_never_sent(
            self, client, three_doctors, ai_on):
        three_doctors.requests[2]["reason"] = "Oncology appointment"
        three_doctors.requests[2]["notes"] = "Consultant referral attached"
        fake = ai_on(_ranking(_entry(2), _entry(4), _entry(3)))
        _suggest(client)
        assert "Oncology appointment" not in fake.prompt
        assert "Consultant referral" not in fake.prompt

    def test_shift_notes_are_never_sent(self, client, three_doctors, ai_on):
        three_doctors.shifts[SHIFT]["notes"] = "Cover for staff bereavement"
        fake = ai_on(_ranking(_entry(2), _entry(4), _entry(3)))
        _suggest(client)
        assert "bereavement" not in fake.prompt

    def test_candidate_projection_is_exactly_the_allowed_fields(self):
        projected = ai_service._llm_candidate({
            "staff_id": 2, "name": "Daniel Reyes", "role": "Doctor",
            "department": "Emergency", "specialisation": "Emergency Medicine",
            "employment_status": "Full-Time", "availability_status": "Available",
            "weekly_ok": True, "eligible": True, "blocked_reason": None,
            "notes": [], "approved_request": None,
        })
        assert set(projected) == {
            "staff_id", "role", "department", "specialisation",
            "employment_status", "availability_status", "eligible",
            "blocking_reason", "department_matches_shift",
            "weekly_availability_matches", "weekly_rostered_hours",
            "current_assignments", "conflicting_assignment"}

    def test_shift_projection_is_exactly_the_allowed_fields(self):
        projected = ai_service._llm_shift({
            "shift_id": 2, "department": "Surgery", "shift_date": "2026-08-25",
            "start_time": "08:00", "end_time": "16:00", "required_role": "Doctor",
            "required_staff_count": 1, "shift_status": "Planned",
            "notes": "Cover for bereavement", "created_at": "x", "updated_at": "y",
        })
        assert set(projected) == {
            "department", "shift_date", "start_time", "end_time",
            "required_role", "required_staff_count"}

    def test_response_context_mirrors_what_was_sent(
            self, client, three_doctors, ai_on):
        """The published context must not be more generous than the prompt."""
        ai_on(_ranking(_entry(2), _entry(4), _entry(3)))
        context = _suggest(client)["context"]
        assert all("name" not in row for row in context["candidates"])
        assert "notes" not in context["shift"]


# ------------------------------------------------------- the prompt itself
class TestPromptArtefact:
    def test_prompt_states_the_model_may_not_decide_eligibility(self):
        system = suggest_staff_prompt.SYSTEM_PROMPT.lower()
        assert "never invent a staff_id" in system
        assert "already been decided" in system
        assert "do not assign anyone" in system

    def test_prompt_presents_weekly_availability_as_advisory(self):
        prompt = suggest_staff_prompt.build_prompt({}, [])
        assert "preference only" in prompt
        assert "still fully eligible" in prompt

    def test_prompt_serialises_only_what_it_is_given(self):
        """build_prompt filters nothing, so the caller owns minimisation."""
        prompt = suggest_staff_prompt.build_prompt(
            {"department": "Surgery"}, [{"staff_id": 2, "role": "Doctor"}])
        assert '"department": "Surgery"' in prompt
        assert '"staff_id": 2' in prompt

    def test_prompt_forbids_invented_operational_facts_and_policy(self):
        system = suggest_staff_prompt.SYSTEM_PROMPT.lower()
        assert "only use facts present in the supplied context" in system
        assert "never invent" in system
        assert "weekly-hours policy" in system
        assert "department mismatch is context, not a blocker" in system


class TestGroundedReasoningContext:
    def test_one_eligible_candidate_has_real_gap_and_blocker_context(
            self, client, stub_database, ai_on):
        _add_weekly_match(stub_database, 2)
        fake = ai_on({
            suggest_staff_prompt.RANKING_KEY: [
                _entry(2, "Available cross-department option with no current rostered hours")],
        })
        body = _suggest(client)
        facts = body["context"]

        assert body["assessment"].startswith("Eligible staff are identified")
        assert facts["coverage_gap"]["shortfall"] == 1
        assert [row["staff_id"] for row in facts["eligible_candidates"]] == [2]
        assert facts["eligible_candidates"][0]["department_matches_shift"] is False
        assert facts["eligible_candidates"][0]["weekly_rostered_hours"] == 0
        assert facts["ineligible_candidates"][0]["blocking_reason"] == "On Leave"
        assert facts["configured_policies"]["weekly_hours_limit"] == {
            "configured": False, "value": None,
            "note": "No weekly-hours policy is configured."}
        assert '"coverage_gap"' in fake.prompt
        assert '"blocking_reason": "On Leave"' in fake.prompt

    def test_weekly_hours_come_from_real_active_assignments(
            self, client, stub_database, ai_on):
        _add_weekly_match(stub_database, 2)
        stub_database.shifts[3] = {
            "shift_id": 3, "department": "Emergency",
            "shift_date": "2026-08-24", "start_time": "09:00",
            "end_time": "14:30", "required_role": "Doctor",
            "required_staff_count": 1, "shift_status": "Planned", "notes": None}
        stub_database.assignments[2] = {
            "assignment_id": 2, "shift_id": 3, "staff_id": 2,
            "assignment_status": "Assigned", "approved_by": None,
            "approved_at": None}
        ai_on(_ranking(_entry(2)))

        candidate = _suggest(client)["context"]["eligible_candidates"][0]
        assert candidate["weekly_rostered_hours"] == 5.5
        assert candidate["current_assignments"][0]["start_time"] == "09:00"

    def test_overlap_is_supplied_as_a_real_blocking_constraint(
            self, client, stub_database, ai_on):
        stub_database.staff[4] = {
            "staff_id": 4, "name": "Ravi Chandran", "role": "Doctor",
            "department": "Surgery", "specialisation": "Orthopaedics",
            "availability_status": "Available", "employment_status": "Part-Time",
            "notes": None}
        stub_database.shifts[3] = {
            "shift_id": 3, "department": "Surgery",
            "shift_date": "2026-08-25", "start_time": "07:00",
            "end_time": "10:00", "required_role": "Doctor",
            "required_staff_count": 1, "shift_status": "Planned", "notes": None}
        stub_database.assignments[2] = {
            "assignment_id": 2, "shift_id": 3, "staff_id": 4,
            "assignment_status": "Assigned", "approved_by": None,
            "approved_at": None}
        ai_on(_ranking(_entry(2)))

        blocked = _suggest(client)["context"]["ineligible_candidates"]
        ravi = next(row for row in blocked if row["staff_id"] == 4)
        assert ravi["blocking_reason"] == "Already rostered 2026-08-25 07:00-10:00"
        assert ravi["conflicting_assignment"]["start_time"] == "07:00"
        assert ravi["weekly_rostered_hours"] == 3
        assert 4 not in {row["staff_id"] for row in
                         _suggest(client)["alternatives"]}

    def test_unconfigured_hours_policy_claim_in_model_prose_is_not_rendered(
            self, client, stub_database, ai_on):
        _add_weekly_match(stub_database, 2)
        ai_on({
            "assessment": "The candidate exceeds the weekly limit and should not work.",
            suggest_staff_prompt.RANKING_KEY: [_entry(2)],
        })
        body = _suggest(client)
        assert body["mode"] == "ai"
        assert "weekly limit" not in body["assessment"]
        assert all("weekly limit" not in row["rationale"]
                   for row in body["suggestions"])

    def test_model_cannot_invent_a_conflict_in_displayed_reasoning(
            self, client, stub_database, ai_on):
        _add_weekly_match(stub_database, 2)
        ai_on(_ranking(_entry(2, "Candidate has a conflicting assignment")))
        body = _suggest(client)
        assert body["mode"] == "ai"
        assert "conflict" not in body["suggestions"][0]["rationale"].lower()
        assert body["suggestions"][0]["conflicting_assignment"] is None

    def test_no_eligible_candidate_reports_actual_recorded_reason(
            self, client, ai_on):
        fake = ai_on(_ranking(_entry(1)))
        body = _suggest(client, shift_id=1)
        assert fake.calls == []
        assert "Already assigned to this shift (1)" in body["note"]


# ------------------------------------------------------ the client itself
class _FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_urlopen(monkeypatch):
    """Drive OllamaClient's real transport without a running Ollama."""
    from services import ollama_client as module

    def _install(body=None, error=None):
        captured = {}

        def _urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            if error is not None:
                raise error
            return _FakeResponse(body)

        monkeypatch.setattr(module.urllib.request, "urlopen", _urlopen)
        return captured

    return _install


class TestOllamaClientTransport:
    def _client(self):
        from services.ollama_client import OllamaClient
        return OllamaClient(base_url="http://localhost:11434",
                            model="llama3", timeout=1)

    def test_parses_the_json_inside_the_response_envelope(self, fake_urlopen):
        fake_urlopen(body=json.dumps({"response": '{"ranking": [{"staff_id": 2}]}'}))
        reply = self._client().generate_json("prompt", system="system")
        assert reply == {"ranking": [{"staff_id": 2}]}

    def test_requests_deterministic_non_streaming_json(self, fake_urlopen):
        captured = fake_urlopen(body=json.dumps({"response": "{}"}))
        self._client().generate_json("rank these", system="be careful")
        assert captured["url"].endswith("/api/generate")
        assert captured["payload"]["format"] == "json"
        assert captured["payload"]["stream"] is False
        assert captured["payload"]["options"]["temperature"] == 0
        assert captured["payload"]["model"] == "llama3"
        assert captured["payload"]["system"] == "be careful"
        assert captured["timeout"] == 1

    def test_timeout_raises_unavailable(self, fake_urlopen):
        fake_urlopen(error=TimeoutError("timed out"))
        with pytest.raises(OllamaError) as excinfo:
            self._client().generate_json("prompt")
        assert excinfo.value.reason == REASON_UNAVAILABLE

    def test_connection_refused_raises_unavailable(self, fake_urlopen):
        fake_urlopen(error=urllib.error.URLError("Connection refused"))
        with pytest.raises(OllamaError) as excinfo:
            self._client().generate_json("prompt")
        assert excinfo.value.reason == REASON_UNAVAILABLE

    def test_http_error_raises_unavailable(self, fake_urlopen):
        """A 404 here usually means the model was never pulled."""
        fake_urlopen(error=urllib.error.HTTPError(
            "http://localhost:11434/api/generate", 404, "Not Found", {}, None))
        with pytest.raises(OllamaError) as excinfo:
            self._client().generate_json("prompt")
        assert excinfo.value.reason == REASON_UNAVAILABLE

    def test_malformed_envelope_raises_invalid_output(self, fake_urlopen):
        fake_urlopen(body="not json at all")
        with pytest.raises(OllamaError) as excinfo:
            self._client().generate_json("prompt")
        assert excinfo.value.reason == REASON_INVALID_OUTPUT

    def test_missing_response_field_raises_invalid_output(self, fake_urlopen):
        fake_urlopen(body=json.dumps({"done": True}))
        with pytest.raises(OllamaError) as excinfo:
            self._client().generate_json("prompt")
        assert excinfo.value.reason == REASON_INVALID_OUTPUT

    def test_model_output_that_is_not_json_raises_invalid_output(self, fake_urlopen):
        fake_urlopen(body=json.dumps({"response": "I think staff 2 is best."}))
        with pytest.raises(OllamaError) as excinfo:
            self._client().generate_json("prompt")
        assert excinfo.value.reason == REASON_INVALID_OUTPUT

    def test_model_output_that_is_not_an_object_raises_invalid_output(
            self, fake_urlopen):
        fake_urlopen(body=json.dumps({"response": "[1, 2, 3]"}))
        with pytest.raises(OllamaError) as excinfo:
            self._client().generate_json("prompt")
        assert excinfo.value.reason == REASON_INVALID_OUTPUT

    def test_no_transport_exception_escapes_as_itself(self, fake_urlopen):
        """Callers catch OllamaError only; a raw urllib error would be a 500."""
        fake_urlopen(error=OSError("socket exploded"))
        with pytest.raises(OllamaError):
            self._client().generate_json("prompt")
