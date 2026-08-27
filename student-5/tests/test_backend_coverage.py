"""Coverage arithmetic tests for the Student 5 backend/API microservice.

``coverage_service`` is the SINGLE SOURCE OF TRUTH for what is filled, short,
spare, and the resulting percentage. These cases were ported from the frontend
suite when that arithmetic moved across the service boundary, so the semantics
they protect are now guarded where the calculation actually happens.

The case names (A-E) are kept from the frontend originals so the two can be
traced to each other.
"""

from __future__ import annotations

import pytest

from services import coverage_service


class _FakeDatabase:
    """Returns a fixed shift list; nothing else is reached by these tests."""

    def __init__(self, shifts):
        self._shifts = shifts

    def list_shifts(self, department=None, shift_date=None, shift_status=None):
        rows = self._shifts
        if department:
            rows = [r for r in rows if r["department"] == department]
        if shift_date:
            rows = [r for r in rows if r["shift_date"] == shift_date]
        if shift_status:
            rows = [r for r in rows if r["shift_status"] == shift_status]
        return rows


@pytest.fixture
def coverage_of(monkeypatch):
    """Compute coverage over arbitrary (required, assigned) pairs.

    The backend analogue of the frontend suite's ``_agg`` helper: it states a
    roster shape directly rather than building it out of real assignments, so
    a case like "one shift with nine people on it" stays expressible.
    """
    def _build(*pairs, department=None, shift_date=None):
        shifts, assigned_by_id = [], {}
        for index, (required, assigned) in enumerate(pairs, start=1):
            shifts.append({
                "shift_id": index, "department": "Emergency",
                "shift_date": "2026-08-24", "start_time": "07:00",
                "end_time": "15:00", "required_role": "Registered Nurse",
                "required_staff_count": required, "shift_status": "Planned",
                "notes": None,
            })
            assigned_by_id[index] = assigned

        monkeypatch.setattr(coverage_service, "database_client",
                            _FakeDatabase(shifts))
        monkeypatch.setattr(
            coverage_service.assignment_service, "active_assignments",
            lambda shift_id: [{"staff_id": n} for n in
                              range(assigned_by_id.get(shift_id, 0))])
        return coverage_service.shift_coverage(
            department=department, shift_date=shift_date)

    return _build


def _summary(result):
    return result["summary"]


# ------------------------------------------------- the per-shift rule
class TestShiftTotals:
    """``shift_totals`` is the whole coverage rule for one shift."""

    def test_shortage(self):
        assert coverage_service.shift_totals(4, 3) == {
            "filled_staff_count": 3, "shortfall": 1, "surplus": 0}

    def test_exact(self):
        assert coverage_service.shift_totals(4, 4) == {
            "filled_staff_count": 4, "shortfall": 0, "surplus": 0}

    def test_surplus_does_not_inflate_filled(self):
        assert coverage_service.shift_totals(2, 3) == {
            "filled_staff_count": 2, "shortfall": 0, "surplus": 1}

    def test_nobody_assigned(self):
        assert coverage_service.shift_totals(3, 0) == {
            "filled_staff_count": 0, "shortfall": 3, "surplus": 0}

    @pytest.mark.parametrize("required,assigned", [
        (1, 9), (2, 3), (5, 0), (3, 3), (1, 1), (7, 4)])
    def test_gap_and_surplus_are_never_both_positive(self, required, assigned):
        totals = coverage_service.shift_totals(required, assigned)
        assert not (totals["shortfall"] and totals["surplus"])
        assert totals["filled_staff_count"] <= required


class TestCoveragePercentage:
    def test_partial(self):
        assert coverage_service.coverage_percentage(3, 4) == 75

    def test_complete(self):
        assert coverage_service.coverage_percentage(4, 4) == 100

    def test_zero_required_is_none_not_zero_or_full(self):
        """No shifts means no coverage to report — the UI shows an em dash."""
        assert coverage_service.coverage_percentage(0, 0) is None

    def test_nothing_filled(self):
        assert coverage_service.coverage_percentage(0, 4) == 0


# ------------------------------------- ported aggregate cases (A-E)
class TestAggregateSemantics:
    """Ported verbatim in intent from the frontend coverage suite."""

    def test_case_a_normal_shortage(self, coverage_of):
        s = _summary(coverage_of((4, 3)))
        assert (s["filled_positions"], s["total_shortfall"],
                s["total_surplus"], s["coverage_pct"]) == (3, 1, 0, 75)

    def test_case_b_exact_coverage(self, coverage_of):
        s = _summary(coverage_of((4, 4)))
        assert (s["filled_positions"], s["total_shortfall"],
                s["total_surplus"], s["coverage_pct"]) == (4, 0, 0, 100)

    def test_case_c_overstaffed_is_capped_at_100(self, coverage_of):
        s = _summary(coverage_of((2, 3)))
        assert (s["filled_positions"], s["total_shortfall"],
                s["total_surplus"]) == (2, 0, 1)
        assert s["coverage_pct"] == 100

    def test_case_d_surplus_cannot_cancel_another_shifts_gap(self, coverage_of):
        """The critical case: A(req 2, asg 3) + B(req 2, asg 1)."""
        s = _summary(coverage_of((2, 3), (2, 1)))
        assert s["required_positions"] == 4
        assert s["assigned_positions"] == 4
        assert s["filled_positions"] == 3
        assert s["total_shortfall"] == 1          # NOT 0
        assert s["total_surplus"] == 1
        assert s["coverage_pct"] == 75            # NOT 100

    def test_case_e_no_demand_is_none_not_full(self, coverage_of):
        s = _summary(coverage_of())
        assert s["coverage_pct"] is None
        assert s["total_shifts"] == 0
        assert s["required_positions"] == 0

    @pytest.mark.parametrize("pairs", [
        ((1, 9),), ((2, 3), (2, 3)), ((1, 5), (4, 0)), ((3, 4), (1, 1))])
    def test_coverage_never_exceeds_100_across_many_shapes(self, coverage_of, pairs):
        pct = _summary(coverage_of(*pairs))["coverage_pct"]
        assert pct is None or pct <= 100, pairs

    def test_equal_totals_still_report_the_real_gap(self, coverage_of):
        """assigned == required overall, yet a shift is genuinely short."""
        s = _summary(coverage_of((2, 3), (2, 1)))
        assert s["assigned_positions"] == s["required_positions"]
        assert s["total_shortfall"] == 1

    def test_totals_are_sums_of_the_per_shift_rows(self, coverage_of):
        """No total may be derived any way other than summing the breakdown."""
        result = coverage_of((4, 3), (2, 3), (5, 0))
        rows, s = result["shifts"], result["summary"]
        assert s["required_positions"] == sum(r["required_staff_count"] for r in rows)
        assert s["assigned_positions"] == sum(r["assigned_staff_count"] for r in rows)
        assert s["filled_positions"] == sum(r["filled_staff_count"] for r in rows)
        assert s["total_shortfall"] == sum(r["shortfall"] for r in rows)
        assert s["total_surplus"] == sum(r["surplus"] for r in rows)


# ------------------------------------------------------- shift counts
class TestShiftCounts:
    def test_each_shift_falls_in_exactly_one_status(self, coverage_of):
        s = _summary(coverage_of((4, 3), (2, 3), (5, 0), (1, 1)))
        assert (s["fully_staffed"], s["unstaffed"], s["overstaffed"]) == (1, 1, 1)
        assert s["total_shifts"] == 4

    def test_understaffed_counts_every_shift_with_a_gap(self, coverage_of):
        """Pre-existing semantics: 'understaffed' INCLUDES the unstaffed.

        Unstaffed is the subset with nobody at all on it, reported separately
        so it can be prioritised, not carved out of the shortage count.
        """
        s = _summary(coverage_of((4, 3), (5, 0)))
        assert s["understaffed"] == 2
        assert s["unstaffed"] == 1

    def test_overstaffed_count_is_shifts_not_positions(self, coverage_of):
        """One shift three staff over is one overstaffed shift, not three."""
        s = _summary(coverage_of((1, 4)))
        assert s["overstaffed"] == 1
        assert s["total_surplus"] == 3


# ------------------------------------------------ contract preservation
class TestResponseContract:
    """Phase A was additive. Nothing existing may have been renamed or lost."""

    PRE_EXISTING_SHIFT_FIELDS = (
        "shift_id", "department", "shift_date", "start_time", "end_time",
        "required_role", "required_staff_count", "assigned_staff_count",
        "shortfall", "coverage_status")

    PRE_EXISTING_SUMMARY_FIELDS = (
        "total_shifts", "fully_staffed", "understaffed", "unstaffed",
        "total_shortfall")

    def test_pre_existing_shift_fields_survive(self, client):
        row = client.get("/api/shifts/coverage").get_json()["shifts"][0]
        for field in self.PRE_EXISTING_SHIFT_FIELDS:
            assert field in row, field

    def test_pre_existing_summary_fields_survive(self, client):
        summary = client.get("/api/shifts/coverage").get_json()["summary"]
        for field in self.PRE_EXISTING_SUMMARY_FIELDS:
            assert field in summary, field

    def test_new_shift_fields_are_exposed(self, client):
        row = client.get("/api/shifts/coverage").get_json()["shifts"][0]
        assert "filled_staff_count" in row
        assert "surplus" in row

    def test_new_summary_fields_are_exposed(self, client):
        summary = client.get("/api/shifts/coverage").get_json()["summary"]
        for field in ("overstaffed", "total_surplus", "required_positions",
                      "assigned_positions", "filled_positions", "coverage_pct"):
            assert field in summary, field

    def test_filters_still_apply_to_the_new_totals(self, client):
        body = client.get("/api/shifts/coverage?department=Surgery").get_json()
        assert body["summary"]["total_shifts"] == 1
        assert body["summary"]["required_positions"] == sum(
            r["required_staff_count"] for r in body["shifts"])

    def test_real_assignments_drive_the_new_fields(self, client):
        """Through the real stub: shift 1 requires 2 and has 1 assigned."""
        rows = {r["shift_id"]: r
                for r in client.get("/api/shifts/coverage").get_json()["shifts"]}
        assert rows[1]["filled_staff_count"] == 1
        assert rows[1]["shortfall"] == 1
        assert rows[1]["surplus"] == 0
