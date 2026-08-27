"""Eligibility tests for the Student 5 backend/API microservice.

These protect the deterministic rules in ``services/eligibility_service.py``,
which is the SINGLE SOURCE OF TRUTH for who may be assigned to a shift. They
were ported from the frontend suite when the rules moved across the service
boundary, so the same cases now guard the one implementation both the manual
candidate list and the AI suggestion endpoint depend on.

Each hard rule gets a test that it BLOCKS, and each advisory/context signal
gets a test that it does NOT. The second half matters as much as the first:
a rule that quietly starts blocking is how a manager loses the ability to
cover another ward's gap.
"""

from __future__ import annotations

import pytest

from services import eligibility_service as elig


def _shift(shift_id=1, date="2026-08-31", start="07:00", end="15:00",
           dept="Emergency", role="Registered Nurse", required=2,
           status="Assigned"):
    return {"shift_id": shift_id, "department": dept, "shift_date": date,
            "start_time": start, "end_time": end, "required_role": role,
            "required_staff_count": required, "shift_status": "Planned",
            "assignment_status": status}


def _person(staff_id=1, name="Amara Okafor", role="Registered Nurse",
            dept="Emergency", availability="Available", employment="Full-Time",
            specialisation=None):
    return {"staff_id": staff_id, "name": name, "role": role, "department": dept,
            "specialisation": specialisation, "availability_status": availability,
            "employment_status": employment}


def _request(staff_id=1, start="2026-08-31", end="2026-09-02",
             status="Approved", reason="Vacation"):
    return {"request_id": 1, "staff_id": staff_id, "start_date": start,
            "end_date": end, "reason": reason, "notes": None,
            "request_status": status}


_MATCHING_WEEK = [{"day_of_week": 0, "start_time": "07:00", "end_time": "15:00"}]


# ------------------------------------------------------------- time helpers
class TestShiftOverlap:
    def test_detects_partial_overlap(self):
        assert elig.shifts_overlap(_shift(1, start="07:00", end="15:00"),
                                   _shift(2, start="14:00", end="22:00")) is True

    def test_adjacent_shifts_do_not_overlap(self):
        """A shift ending as another begins is back-to-back, not a clash."""
        assert elig.shifts_overlap(_shift(1, start="07:00", end="15:00"),
                                   _shift(2, start="15:00", end="23:00")) is False

    def test_different_days_do_not_overlap(self):
        assert elig.shifts_overlap(_shift(1, date="2026-08-31"),
                                   _shift(2, date="2026-09-01")) is False

    def test_overnight_shift_runs_into_the_next_day(self):
        night = _shift(1, date="2026-08-31", start="23:00", end="07:00")
        morning = _shift(2, date="2026-09-01", start="06:00", end="14:00")
        assert elig.shifts_overlap(night, morning) is True

    def test_malformed_shift_date_does_not_raise(self):
        assert elig.shifts_overlap(_shift(1, date="not-a-date"), _shift(2)) is False


class TestWeeklyCover:
    def test_exact_match_is_covered(self):
        assert elig.weekly_covers_shift(_MATCHING_WEEK,
                                        _shift(date="2026-08-31")) is True

    def test_partial_cover_is_not_cover(self):
        """Availability must cover the WHOLE window, not merely touch it."""
        periods = [{"day_of_week": 0, "start_time": "07:00", "end_time": "14:00"}]
        assert elig.weekly_covers_shift(periods, _shift(date="2026-08-31")) is False

    def test_overnight_period_covers_overnight_shift(self):
        periods = [{"day_of_week": 0, "start_time": "23:00", "end_time": "07:00"}]
        night = _shift(date="2026-08-31", start="23:00", end="07:00")
        assert elig.weekly_covers_shift(periods, night) is True

    def test_empty_pattern_covers_nothing(self):
        assert elig.weekly_covers_shift([], _shift()) is False


# ------------------------------------------------------- HARD RULES: block
class TestHardRules:
    def test_role_mismatch_is_blocked(self):
        result = elig.evaluate_candidate(
            _person(role="Doctor"), _shift(), set(), [], [])
        assert result["eligible"] is False
        assert result["blocked_reason"] == "Role mismatch"

    def test_unavailable_is_blocked(self):
        result = elig.evaluate_candidate(
            _person(availability="Unavailable"), _shift(), set(), [], [])
        assert result["eligible"] is False
        assert result["blocked_reason"] == "Unavailable"

    def test_on_leave_is_blocked(self):
        result = elig.evaluate_candidate(
            _person(availability="On Leave"), _shift(), set(), [], [])
        assert result["eligible"] is False
        assert result["blocked_reason"] == "On Leave"

    def test_approved_request_covering_the_shift_is_blocked(self):
        result = elig.evaluate_candidate(
            _person(), _shift(date="2026-09-01"), set(), [], [],
            [_request(start="2026-08-31", end="2026-09-02")])
        assert result["eligible"] is False
        assert result["blocked_reason"] == "Temporarily unavailable 31 Aug – 2 Sep"

    def test_approved_request_blocking_is_inclusive_of_the_first_day(self):
        result = elig.evaluate_candidate(
            _person(), _shift(date="2026-08-31"), set(), [], [],
            [_request(start="2026-08-31", end="2026-09-02")])
        assert result["eligible"] is False

    def test_approved_request_blocking_is_inclusive_of_the_last_day(self):
        result = elig.evaluate_candidate(
            _person(), _shift(date="2026-09-02"), set(), [], [],
            [_request(start="2026-08-31", end="2026-09-02")])
        assert result["eligible"] is False

    def test_single_day_request_label_does_not_repeat_the_date(self):
        result = elig.evaluate_candidate(
            _person(), _shift(date="2026-08-31"), set(), [], [],
            [_request(start="2026-08-31", end="2026-08-31")])
        assert result["blocked_reason"] == "Temporarily unavailable 31 Aug"

    def test_overlapping_assignment_is_blocked(self):
        target = _shift(shift_id=5, start="07:00", end="15:00")
        clash = _shift(shift_id=9, start="08:00", end="16:00")
        result = elig.evaluate_candidate(_person(), target, set(), [clash], [])
        assert result["eligible"] is False
        assert "Already rostered" in result["blocked_reason"]

    def test_already_assigned_to_this_shift_is_blocked(self):
        result = elig.evaluate_candidate(_person(), _shift(), {1}, [], [])
        assert result["eligible"] is False
        assert "Already assigned" in result["blocked_reason"]

    @pytest.mark.parametrize("status", ["Cancelled", "Declined"])
    def test_inactive_assignment_does_not_clash(self, status):
        """A withdrawn assignment no longer occupies a place on the shift."""
        target = _shift(shift_id=5)
        withdrawn = _shift(shift_id=9, start="08:00", end="16:00", status=status)
        result = elig.evaluate_candidate(
            _person(), target, set(), [withdrawn], _MATCHING_WEEK)
        assert result["eligible"] is True

    def test_the_shift_itself_is_not_counted_as_a_clash(self):
        """The candidate's own row for this shift must not block them."""
        target = _shift(shift_id=5)
        result = elig.evaluate_candidate(
            _person(), target, set(), [target], _MATCHING_WEEK)
        assert result["eligible"] is True


class TestRulePrecedence:
    def test_availability_is_reported_before_a_downstream_clash(self):
        """The truer explanation wins: they are not free at all, rota aside."""
        clash = _shift(shift_id=9, start="08:00", end="16:00")
        result = elig.evaluate_candidate(
            _person(availability="On Leave"), _shift(shift_id=5), set(), [clash], [])
        assert result["blocked_reason"] == "On Leave"

    def test_unavailable_outranks_a_role_mismatch(self):
        """The reason shown should be the operational one, not the rota one."""
        result = elig.evaluate_candidate(
            _person(role="Doctor", availability="Unavailable"),
            _shift(), set(), [], [])
        assert result["blocked_reason"] == "Unavailable"

    def test_available_is_not_a_free_pass(self):
        """Being Available stops blocking at that step, nothing further."""
        clash = _shift(shift_id=9, start="08:00", end="16:00")
        result = elig.evaluate_candidate(
            _person(availability="Available"), _shift(shift_id=5),
            set(), [clash], _MATCHING_WEEK)
        assert result["eligible"] is False
        assert "Already rostered" in result["blocked_reason"]

    def test_already_assigned_outranks_every_other_reason(self):
        result = elig.evaluate_candidate(
            _person(role="Doctor", availability="Unavailable"), _shift(), {1}, [], [])
        assert result["blocked_reason"] == "Already assigned to this shift"


# ------------------------------------------------- ADVISORY: never blocks
class TestAdvisoryWeeklyAvailability:
    def test_matching_weekly_availability_is_flagged_ok(self):
        result = elig.evaluate_candidate(
            _person(), _shift(), set(), [], _MATCHING_WEEK)
        assert result["eligible"] is True
        assert result["weekly_ok"] is True
        assert any(note["ok"] for note in result["notes"])

    def test_outside_weekly_availability_is_advisory_not_blocking(self):
        """The assign endpoint permits this, so it must warn, never block."""
        result = elig.evaluate_candidate(_person(), _shift(), set(), [], [])
        assert result["eligible"] is True
        assert result["weekly_ok"] is False
        assert result["blocked_reason"] is None
        assert any("Outside weekly availability" in n["text"]
                   for n in result["notes"])

    def test_advisory_note_is_recorded_even_when_blocked(self):
        """A manager sees the whole picture, not one reason at a time."""
        result = elig.evaluate_candidate(
            _person(availability="On Leave"), _shift(), set(), [], _MATCHING_WEEK)
        assert result["eligible"] is False
        assert result["weekly_ok"] is True


# --------------------------------------------------- CONTEXT: never blocks
class TestContextSignals:
    def test_other_department_remains_assignable(self):
        """Department is an ordering preference, never an eligibility rule."""
        result = elig.evaluate_candidate(
            _person(dept="Intensive Care"), _shift(dept="Emergency"),
            set(), [], _MATCHING_WEEK)
        assert result["eligible"] is True
        assert result["blocked_reason"] is None
        assert result["department"] == "Intensive Care"

    def test_specialisation_never_blocks(self):
        result = elig.evaluate_candidate(
            _person(specialisation="Palliative Care"),
            _shift(), set(), [], _MATCHING_WEEK)
        assert result["eligible"] is True
        assert result["specialisation"] == "Palliative Care"

    @pytest.mark.parametrize("employment", ["Full-Time", "Part-Time",
                                            "Casual", "Contract"])
    def test_employment_status_never_blocks(self, employment):
        result = elig.evaluate_candidate(
            _person(employment=employment), _shift(), set(), [], _MATCHING_WEEK)
        assert result["eligible"] is True


class TestNonBlockingRequestStates:
    @pytest.mark.parametrize("status", ["Pending", "Rejected", "Cancelled"])
    def test_only_an_approved_request_blocks(self, status):
        """A pending request is still a question, not an answer."""
        result = elig.evaluate_candidate(
            _person(), _shift(date="2026-09-01"), set(), [], _MATCHING_WEEK,
            [_request(start="2026-08-31", end="2026-09-02", status=status)])
        assert result["eligible"] is True

    def test_approved_request_outside_the_shift_date_does_not_block(self):
        result = elig.evaluate_candidate(
            _person(), _shift(date="2026-09-05"), set(), [], _MATCHING_WEEK,
            [_request(start="2026-08-31", end="2026-09-02")])
        assert result["eligible"] is True
        assert result["approved_request"] is None

    def test_the_requests_argument_is_optional(self):
        """Callers with no absence data to supply must still work."""
        result = elig.evaluate_candidate(
            _person(), _shift(), set(), [], _MATCHING_WEEK)
        assert result["eligible"] is True
        assert result["approved_request"] is None


class TestNoScoring:
    """Eligibility is a decision, not a total. Nothing may reintroduce one."""

    def test_candidate_carries_no_score(self):
        result = elig.evaluate_candidate(
            _person(), _shift(), set(), [], _MATCHING_WEEK)
        assert "score" not in result
        assert "reasons" not in result

    def test_context_signals_cannot_outweigh_a_hard_rule(self):
        """Same department, right specialisation, permanent — still blocked."""
        perfect_but_unavailable = _person(
            dept="Emergency", availability="Unavailable",
            employment="Full-Time", specialisation="Triage")
        result = elig.evaluate_candidate(
            perfect_but_unavailable, _shift(dept="Emergency"),
            set(), [], _MATCHING_WEEK)
        assert result["eligible"] is False


# --------------------------------------------------------- the orchestration
class TestCandidatesForShift:
    """Rules applied to real records through the stub database service."""

    def test_returns_blocked_candidates_annotated_not_removed(self, client):
        # Shift 2 requires a Doctor: staff 2 (Available) and staff 3 (On Leave).
        body = client.get("/api/shifts/2/candidates").get_json()
        assert body["count"] == 2
        assert body["eligible_count"] == 1
        by_id = {row["staff_id"]: row for row in body["candidates"]}
        assert by_id[2]["eligible"] is True
        assert by_id[3]["eligible"] is False
        assert by_id[3]["blocked_reason"] == "On Leave"

    def test_only_the_required_role_is_considered(self, client):
        body = client.get("/api/shifts/2/candidates").get_json()
        assert {row["role"] for row in body["candidates"]} == {"Doctor"}

    def test_eligible_candidates_sort_before_blocked_ones(self, client):
        body = client.get("/api/shifts/2/candidates").get_json()
        eligibility = [row["eligible"] for row in body["candidates"]]
        assert eligibility == sorted(eligibility, reverse=True)

    def test_already_assigned_staff_are_reported_and_blocked(self, client):
        # Staff 1 is the only Registered Nurse and is already on shift 1.
        body = client.get("/api/shifts/1/candidates").get_json()
        assert body["already_assigned_staff_ids"] == [1]
        assert body["eligible_count"] == 0
        assert body["candidates"][0]["blocked_reason"] == "Already assigned to this shift"

    def test_approved_request_blocks_through_the_real_lookup(self, client):
        # Staff 2 has an Approved request covering 2026-08-24.
        created = client.post("/api/shifts", json={
            "department": "Surgery", "shift_date": "2026-08-24",
            "start_time": "08:00", "end_time": "16:00",
            "required_role": "Doctor", "required_staff_count": 1}).get_json()
        body = client.get(
            f"/api/shifts/{created['shift']['shift_id']}/candidates").get_json()
        by_id = {row["staff_id"]: row for row in body["candidates"]}
        assert by_id[2]["eligible"] is False
        assert by_id[2]["blocked_reason"].startswith("Temporarily unavailable")
        # Requests are grouped by staff_id, so staff 2's absence must not be
        # applied to staff 3 — who is blocked for their own, different reason.
        assert by_id[3]["blocked_reason"] == "On Leave"

    def test_overlapping_assignment_blocks_through_the_real_lookup(self, client):
        client.post("/api/shifts/2/assign", json={"staff_id": 2})
        created = client.post("/api/shifts", json={
            "department": "Surgery", "shift_date": "2026-08-25",
            "start_time": "12:00", "end_time": "20:00",
            "required_role": "Doctor", "required_staff_count": 1}).get_json()
        body = client.get(
            f"/api/shifts/{created['shift']['shift_id']}/candidates").get_json()
        by_id = {row["staff_id"]: row for row in body["candidates"]}
        assert by_id[2]["eligible"] is False
        assert "Already rostered" in by_id[2]["blocked_reason"]

    def test_unknown_shift_returns_404(self, client):
        assert client.get("/api/shifts/999/candidates").status_code == 404

    def test_requires_the_manager_role(self, client):
        response = client.get("/api/shifts/2/candidates",
                              headers={"X-HOMS-Role": "Employee",
                                       "X-HOMS-Staff-Id": "2"})
        assert response.status_code == 403
