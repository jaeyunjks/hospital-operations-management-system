"""API tests for the Student 5 backend/API microservice.

Covers every endpoint required by prompt artefact S5-BE-001, plus validation
and error handling. Run with:  pytest student-5/tests -v
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------- service
class TestServiceEndpoints:
    def test_health_reports_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.get_json()["service"] == "student-5-backend"

    def test_api_index_lists_endpoints(self, client):
        response = client.get("/api")
        assert response.status_code == 200
        assert "staff" in response.get_json()["endpoints"]

    def test_unknown_endpoint_returns_json_404(self, client):
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        assert response.get_json()["error"] == "not_found"

    def test_wrong_method_returns_405(self, client):
        assert client.delete("/api/staff").status_code == 405


# ------------------------------------------------------------------ staff
class TestStaffEndpoints:
    def test_list_staff(self, client):
        response = client.get("/api/staff")
        assert response.status_code == 200
        assert response.get_json()["count"] == 3

    def test_list_staff_filtered_by_department(self, client):
        body = client.get("/api/staff?department=Emergency").get_json()
        assert body["count"] == 2

    def test_list_staff_rejects_invalid_availability(self, client):
        response = client.get("/api/staff?availability_status=Bogus")
        assert response.status_code == 400

    def test_search_staff_by_term(self, client):
        body = client.get("/api/staff/search?q=triage").get_json()
        assert body["count"] == 1
        assert body["staff"][0]["name"] == "Amara Okafor"

    def test_search_staff_without_term_returns_all(self, client):
        assert client.get("/api/staff/search").get_json()["count"] == 3

    def test_search_staff_rejects_blank_term(self, client):
        assert client.get("/api/staff/search?q=%20").status_code == 400

    def test_update_availability(self, client):
        response = client.put("/api/staff/1/availability",
                              json={"availability_status": "On Leave"})
        assert response.status_code == 200
        assert response.get_json()["staff"]["availability_status"] == "On Leave"

    def test_update_availability_rejects_invalid_value(self, client):
        response = client.put("/api/staff/1/availability",
                              json={"availability_status": "Bogus"})
        assert response.status_code == 400
        assert response.get_json()["error"] == "validation_error"

    def test_update_availability_requires_field(self, client):
        assert client.put("/api/staff/1/availability", json={}).status_code == 400

    def test_update_availability_unknown_staff(self, client):
        response = client.put("/api/staff/999/availability",
                              json={"availability_status": "Available"})
        assert response.status_code == 404


# ----------------------------------------------------------------- shifts
class TestShiftEndpoints:
    VALID = {"department": "Emergency", "shift_date": "2026-09-01",
             "start_time": "07:00", "end_time": "15:00",
             "required_role": "Registered Nurse", "required_staff_count": 2}

    def test_list_shifts(self, client):
        assert client.get("/api/shifts").get_json()["count"] == 2

    def test_list_shifts_filtered_by_date(self, client):
        assert client.get("/api/shifts?shift_date=2026-08-24").get_json()["count"] == 1

    def test_list_shifts_rejects_bad_date(self, client):
        assert client.get("/api/shifts?shift_date=01-09-2026").status_code == 400

    def test_create_shift(self, client):
        response = client.post("/api/shifts", json=self.VALID)
        assert response.status_code == 201
        assert response.get_json()["shift"]["department"] == "Emergency"

    def test_create_shift_requires_fields(self, client):
        response = client.post("/api/shifts", json={"department": "Emergency"})
        assert response.status_code == 400
        assert "missing_fields" in response.get_json()["details"]

    def test_create_shift_rejects_bad_date(self, client):
        payload = {**self.VALID, "shift_date": "01-09-2026"}
        assert client.post("/api/shifts", json=payload).status_code == 400

    def test_create_shift_rejects_bad_time(self, client):
        payload = {**self.VALID, "start_time": "7am"}
        assert client.post("/api/shifts", json=payload).status_code == 400

    def test_create_shift_rejects_zero_staff_count(self, client):
        payload = {**self.VALID, "required_staff_count": 0}
        assert client.post("/api/shifts", json=payload).status_code == 400

    def test_create_shift_rejects_equal_start_and_end(self, client):
        payload = {**self.VALID, "end_time": "07:00"}
        assert client.post("/api/shifts", json=payload).status_code == 400

    def test_update_shift(self, client):
        response = client.put("/api/shifts/1", json={"shift_status": "Open"})
        assert response.status_code == 200
        assert response.get_json()["shift"]["shift_status"] == "Open"

    def test_update_shift_rejects_invalid_status(self, client):
        assert client.put("/api/shifts/1", json={"shift_status": "Bogus"}).status_code == 400

    def test_update_shift_requires_updatable_field(self, client):
        assert client.put("/api/shifts/1", json={"unknown": "x"}).status_code == 400

    def test_update_unknown_shift(self, client):
        assert client.put("/api/shifts/999", json={"shift_status": "Open"}).status_code == 404

    def test_delete_shift(self, client):
        assert client.delete("/api/shifts/1").status_code == 200
        assert client.get("/api/shifts/1").status_code == 404

    def test_delete_unknown_shift(self, client):
        assert client.delete("/api/shifts/999").status_code == 404


# ------------------------------------------------------------ assignments
class TestAssignmentEndpoints:
    def test_assign_staff(self, client):
        response = client.post("/api/shifts/2/assign", json={"staff_id": 2})
        assert response.status_code == 201
        assert response.get_json()["assignment"]["assignment_status"] == "Assigned"

    def test_assign_records_approver(self, client):
        response = client.post("/api/shifts/2/assign",
                               json={"staff_id": 2, "approved_by": "Nadia Whitfield"})
        assert response.get_json()["assignment"]["approved_by"] == "Nadia Whitfield"

    def test_assign_duplicate_conflicts(self, client):
        response = client.post("/api/shifts/1/assign", json={"staff_id": 1})
        assert response.status_code == 409
        assert response.get_json()["error"] == "conflict"

    def test_assign_unknown_staff(self, client):
        assert client.post("/api/shifts/1/assign", json={"staff_id": 999}).status_code == 404

    def test_assign_unknown_shift(self, client):
        assert client.post("/api/shifts/999/assign", json={"staff_id": 1}).status_code == 404

    def test_assign_requires_staff_id(self, client):
        assert client.post("/api/shifts/1/assign", json={}).status_code == 400

    def test_assign_rejects_non_integer_staff_id(self, client):
        assert client.post("/api/shifts/1/assign", json={"staff_id": "one"}).status_code == 400

    def test_unassign_cancels_rather_than_deletes(self, client):
        response = client.put("/api/shifts/1/unassign", json={"staff_id": 1})
        assert response.status_code == 200
        assert response.get_json()["assignment"]["assignment_status"] == "Cancelled"

    def test_unassign_staff_not_on_shift(self, client):
        assert client.put("/api/shifts/1/unassign", json={"staff_id": 2}).status_code == 404

    def test_reassigning_cancelled_staff_reinstates(self, client):
        client.put("/api/shifts/1/unassign", json={"staff_id": 1})
        response = client.post("/api/shifts/1/assign", json={"staff_id": 1})
        assert response.status_code == 201
        assert response.get_json()["assignment"]["assignment_status"] == "Assigned"

    def test_list_shift_assignments(self, client):
        body = client.get("/api/shifts/1/assignments").get_json()
        assert body["count"] == 1


# -------------------------------------------------------------- coverage
class TestCoverageEndpoint:
    def test_coverage_returns_summary_and_breakdown(self, client):
        body = client.get("/api/shifts/coverage").get_json()
        assert body["summary"]["total_shifts"] == 2
        assert len(body["shifts"]) == 2

    def test_coverage_identifies_shortfall(self, client):
        rows = {r["shift_id"]: r for r in client.get("/api/shifts/coverage").get_json()["shifts"]}
        # Shift 1 needs 2, has 1 assigned.
        assert rows[1]["shortfall"] == 1
        assert rows[1]["coverage_status"] == "Understaffed"
        # Shift 2 needs 1, has none.
        assert rows[2]["coverage_status"] == "Unstaffed"

    def test_coverage_reflects_new_assignment(self, client):
        client.post("/api/shifts/2/assign", json={"staff_id": 2})
        rows = {r["shift_id"]: r for r in client.get("/api/shifts/coverage").get_json()["shifts"]}
        assert rows[2]["coverage_status"] == "Fully staffed"

    def test_coverage_filtered_by_department(self, client):
        body = client.get("/api/shifts/coverage?department=Surgery").get_json()
        assert body["summary"]["total_shifts"] == 1

    def test_cancelled_assignment_does_not_count(self, client):
        client.put("/api/shifts/1/unassign", json={"staff_id": 1})
        rows = {r["shift_id"]: r for r in client.get("/api/shifts/coverage").get_json()["shifts"]}
        assert rows[1]["assigned_staff_count"] == 0


# ------------------------------------------------------------- AI-ready
class TestAiReadyEndpoints:
    def test_suggest_staff_returns_ranked_candidates(self, client):
        response = client.post("/api/shifts/suggest-staff", json={"shift_id": 1})
        assert response.status_code == 200
        body = response.get_json()
        assert body["ai_enabled"] is False
        assert body["mode"] == "rule-based"
        assert len(body["suggestions"]) > 0

    def test_suggest_staff_ranks_role_match_highest(self, client):
        body = client.post("/api/shifts/suggest-staff", json={"shift_id": 1}).get_json()
        scores = [s["score"] for s in body["suggestions"]]
        assert scores == sorted(scores, reverse=True)

    def test_suggest_staff_excludes_already_assigned(self, client):
        body = client.post("/api/shifts/suggest-staff", json={"shift_id": 1}).get_json()
        assert 1 not in [s["staff_id"] for s in body["suggestions"]]

    def test_suggest_staff_prepares_llm_context(self, client):
        body = client.post("/api/shifts/suggest-staff", json={"shift_id": 1}).get_json()
        assert body["context"]["task"] == "suggest_staff_for_shift"
        assert "model" in body["context"]

    def test_suggest_staff_respects_limit(self, client):
        body = client.post("/api/shifts/suggest-staff",
                           json={"shift_id": 1, "limit": 1}).get_json()
        assert len(body["suggestions"]) == 1

    def test_suggest_staff_requires_shift_id(self, client):
        assert client.post("/api/shifts/suggest-staff", json={}).status_code == 400

    def test_suggest_staff_rejects_bad_limit(self, client):
        response = client.post("/api/shifts/suggest-staff",
                               json={"shift_id": 1, "limit": 0})
        assert response.status_code == 400

    def test_suggest_staff_unknown_shift(self, client):
        assert client.post("/api/shifts/suggest-staff",
                           json={"shift_id": 999}).status_code == 404

    def test_coverage_summary_returns_headline(self, client):
        response = client.post("/api/shifts/coverage-summary", json={})
        assert response.status_code == 200
        body = response.get_json()
        assert body["ai_enabled"] is False
        assert "headline" in body

    def test_coverage_summary_prepares_llm_context(self, client):
        body = client.post("/api/shifts/coverage-summary", json={}).get_json()
        assert body["context"]["task"] == "summarise_staffing_coverage"

    def test_coverage_summary_rejects_bad_date(self, client):
        response = client.post("/api/shifts/coverage-summary",
                               json={"shift_date": "tomorrow"})
        assert response.status_code == 400


# ------------------------------------------------- staff detail & filters
class TestStaffDetailAndEmploymentFilter:
    def test_get_staff_by_id(self, client):
        response = client.get("/api/staff/1")
        assert response.status_code == 200
        assert response.get_json()["staff"]["name"] == "Amara Okafor"

    def test_get_unknown_staff(self, client):
        assert client.get("/api/staff/999").status_code == 404

    def test_search_path_not_shadowed_by_id_route(self, client):
        """/api/staff/search must still resolve as the search endpoint."""
        assert client.get("/api/staff/search?q=amara").status_code == 200

    def test_employment_status_filter(self, client):
        body = client.get("/api/staff?employment_status=Full-Time").get_json()
        assert body["count"] == 3
        assert all(s["employment_status"] == "Full-Time" for s in body["staff"])

    def test_employment_status_rejects_invalid_value(self, client):
        response = client.get("/api/staff?employment_status=Bogus")
        assert response.status_code == 400
        assert response.get_json()["error"] == "validation_error"

    def test_search_combines_with_employment_filter(self, client):
        body = client.get("/api/staff/search?q=a&employment_status=Full-Time").get_json()
        assert all(s["employment_status"] == "Full-Time" for s in body["staff"])


# ------------------------------------------------- weekly availability
class TestWeeklyAvailability:
    URL = "/api/staff/1/weekly-availability"

    def _periods(self, *specs):
        return {"periods": [{"day_of_week": d, "start_time": s, "end_time": e}
                            for d, s, e in specs]}

    def test_get_returns_seeded_pattern(self, client):
        body = client.get(self.URL).get_json()
        assert body["staff_id"] == 1
        assert body["count"] >= 1
        assert body["periods"][0]["start_time"] == "07:00"

    def test_get_unknown_staff(self, client):
        assert client.get("/api/staff/999/weekly-availability").status_code == 404

    def test_replace_pattern(self, client):
        response = client.put(self.URL, json=self._periods(
            (0, "07:00", "15:00"), (2, "15:00", "23:00")))
        assert response.status_code == 200
        assert response.get_json()["count"] == 2

    def test_replace_with_empty_list_clears_pattern(self, client):
        assert client.put(self.URL, json={"periods": []}).get_json()["count"] == 0

    def test_overnight_period_is_valid(self, client):
        """end_time < start_time is an overnight period, not a malformed one."""
        response = client.put(self.URL, json=self._periods((3, "23:00", "07:00")))
        assert response.status_code == 200

    def test_adjacent_periods_do_not_overlap(self, client):
        response = client.put(self.URL, json=self._periods(
            (0, "07:00", "15:00"), (0, "15:00", "23:00")))
        assert response.status_code == 200

    def test_rejects_overlapping_periods(self, client):
        response = client.put(self.URL, json=self._periods(
            (0, "07:00", "15:00"), (0, "14:00", "20:00")))
        assert response.status_code == 400
        assert response.get_json()["error"] == "validation_error"

    def test_rejects_exact_duplicate(self, client):
        response = client.put(self.URL, json=self._periods(
            (0, "07:00", "15:00"), (0, "07:00", "15:00")))
        assert response.status_code == 400

    def test_rejects_overnight_overlapping_next_day(self, client):
        """Mon 23:00-07:00 spills into Tuesday and must clash with Tue 02:00."""
        response = client.put(self.URL, json=self._periods(
            (0, "23:00", "07:00"), (1, "02:00", "06:00")))
        assert response.status_code == 400

    def test_rejects_week_boundary_overlap(self, client):
        """Sun 23:00-07:00 wraps into Monday and must clash with Mon 02:00."""
        response = client.put(self.URL, json=self._periods(
            (6, "23:00", "07:00"), (0, "02:00", "05:00")))
        assert response.status_code == 400

    def test_rejects_zero_length_period(self, client):
        response = client.put(self.URL, json=self._periods((0, "07:00", "07:00")))
        assert response.status_code == 400

    def test_rejects_invalid_day(self, client):
        response = client.put(self.URL, json=self._periods((9, "07:00", "15:00")))
        assert response.status_code == 400

    def test_rejects_invalid_time(self, client):
        response = client.put(self.URL, json=self._periods((0, "7am", "15:00")))
        assert response.status_code == 400

    def test_rejects_non_list_payload(self, client):
        assert client.put(self.URL, json={"periods": "monday"}).status_code == 400

    def test_replace_unknown_staff(self, client):
        response = client.put("/api/staff/999/weekly-availability",
                              json={"periods": []})
        assert response.status_code == 404

    def test_no_availability_state_is_persisted(self, client):
        """The model is sparse: rows are available periods, with no state field."""
        client.put(self.URL, json=self._periods((0, "07:00", "15:00")))
        period = client.get(self.URL).get_json()["periods"][0]
        assert "availability_state" not in period


# ==========================================================================
# Scenario C — employee unavailability requests
# ==========================================================================
# Two things are being proved here: the lifecycle is genuinely one-way, and
# a decision changes ONLY the request. Nothing in this feature is permitted
# to move staff on or off shifts, or to alter operational availability.

MANAGER = {"X-HOMS-Role": "Staff Manager"}


def _employee(staff_id):
    return {"X-HOMS-Role": "Employee", "X-HOMS-Staff-Id": str(staff_id)}


class TestRequestCreation:
    URL = "/api/staff/1/unavailability-requests"

    def test_employee_creates_own_request(self, client):
        response = client.post(self.URL, headers=_employee(1), json={
            "start_date": "2026-10-01", "end_date": "2026-10-03",
            "reason": "Vacation", "notes": "Away."})
        assert response.status_code == 201
        record = response.get_json()["request"]
        assert record["request_status"] == "Pending"
        assert record["staff_id"] == 1

    def test_new_request_has_no_reviewer(self, client):
        """A pending request has not been decided, so it must carry no
        decision metadata — not an empty string, not a timestamp."""
        record = client.post(self.URL, headers=_employee(1), json={
            "start_date": "2026-10-01", "end_date": "2026-10-01",
            "reason": "Personal"}).get_json()["request"]
        assert record["reviewed_by"] is None
        assert record["reviewed_at"] is None

    def test_single_day_request_is_allowed(self, client):
        response = client.post(self.URL, headers=_employee(1), json={
            "start_date": "2026-10-05", "end_date": "2026-10-05",
            "reason": "Personal"})
        assert response.status_code == 201

    def test_notes_are_optional(self, client):
        response = client.post(self.URL, headers=_employee(1), json={
            "start_date": "2026-10-07", "end_date": "2026-10-08",
            "reason": "Study leave"})
        assert response.status_code == 201
        assert response.get_json()["request"]["notes"] is None

    def test_end_before_start_is_rejected(self, client):
        response = client.post(self.URL, headers=_employee(1), json={
            "start_date": "2026-10-09", "end_date": "2026-10-01",
            "reason": "Personal"})
        assert response.status_code == 400

    def test_missing_reason_is_rejected(self, client):
        response = client.post(self.URL, headers=_employee(1), json={
            "start_date": "2026-10-01", "end_date": "2026-10-02"})
        assert response.status_code == 400

    def test_malformed_date_is_rejected(self, client):
        response = client.post(self.URL, headers=_employee(1), json={
            "start_date": "01/10/2026", "end_date": "2026-10-02",
            "reason": "Personal"})
        assert response.status_code == 400

    def test_unknown_staff_is_404(self, client):
        response = client.post("/api/staff/999/unavailability-requests",
                               headers=_employee(999), json={
                                   "start_date": "2026-10-01",
                                   "end_date": "2026-10-02", "reason": "Personal"})
        assert response.status_code == 404


class TestRequestOverlap:
    """Pending and Approved requests hold a period; Rejected and Cancelled
    ones release it."""
    URL = "/api/staff/1/unavailability-requests"

    def _post(self, client, start, end):
        return client.post(self.URL, headers=_employee(1), json={
            "start_date": start, "end_date": end, "reason": "Personal"})

    def test_overlapping_pending_is_rejected(self, client):
        # Stub request 1 is Pending for staff 1 across 1–3 September.
        assert self._post(client, "2026-09-02", "2026-09-04").status_code == 409

    def test_exact_duplicate_is_rejected(self, client):
        assert self._post(client, "2026-09-01", "2026-09-03").status_code == 409

    def test_enclosing_period_is_rejected(self, client):
        assert self._post(client, "2026-08-30", "2026-09-10").status_code == 409

    def test_touching_boundary_is_an_overlap(self, client):
        """Dates are inclusive, so sharing the boundary day is a real clash."""
        assert self._post(client, "2026-09-03", "2026-09-05").status_code == 409

    def test_adjacent_period_is_allowed(self, client):
        assert self._post(client, "2026-09-04", "2026-09-06").status_code == 201

    def test_cancelled_request_does_not_block(self, client):
        # Stub request 4 is Cancelled for staff 1 across 20–21 September.
        assert self._post(client, "2026-09-20", "2026-09-21").status_code == 201

    def test_rejected_request_does_not_block(self, client):
        # Stub request 3 is Rejected for staff 3 across 10–11 September.
        response = client.post("/api/staff/3/unavailability-requests",
                               headers=_employee(3), json={
                                   "start_date": "2026-09-10",
                                   "end_date": "2026-09-11", "reason": "Vacation"})
        assert response.status_code == 201


class TestRequestLifecycle:
    def test_approve_records_decision(self, client):
        response = client.put("/api/unavailability-requests/1/review",
                              headers=MANAGER,
                              json={"decision": "Approved",
                                    "reviewed_by": "Nadia Whitfield"})
        assert response.status_code == 200
        record = response.get_json()["request"]
        assert record["request_status"] == "Approved"
        assert record["reviewed_by"] == "Nadia Whitfield"
        assert record["reviewed_at"] is not None

    def test_reject_records_decision(self, client):
        record = client.put("/api/unavailability-requests/1/review",
                            headers=MANAGER,
                            json={"decision": "Rejected",
                                  "reviewed_by": "Nadia Whitfield"}
                            ).get_json()["request"]
        assert record["request_status"] == "Rejected"
        assert record["reviewed_by"] == "Nadia Whitfield"

    def test_employee_cancels_own_pending_request(self, client):
        response = client.put(
            "/api/staff/1/unavailability-requests/1/cancel", headers=_employee(1))
        assert response.status_code == 200
        assert response.get_json()["request"]["request_status"] == "Cancelled"

    def test_cancelling_leaves_no_reviewer(self, client):
        """Withdrawing is the employee's own act, not a management decision,
        so it must not populate the reviewer fields."""
        record = client.put("/api/staff/1/unavailability-requests/1/cancel",
                            headers=_employee(1)).get_json()["request"]
        assert record["reviewed_by"] is None
        assert record["reviewed_at"] is None

    def test_invalid_decision_is_rejected(self, client):
        response = client.put("/api/unavailability-requests/1/review",
                              headers=MANAGER,
                              json={"decision": "Maybe", "reviewed_by": "N"})
        assert response.status_code == 400

    def test_review_of_unknown_request_is_404(self, client):
        response = client.put("/api/unavailability-requests/999/review",
                              headers=MANAGER,
                              json={"decision": "Approved", "reviewed_by": "N"})
        assert response.status_code == 404

    @pytest.mark.parametrize("request_id,decision", [
        (2, "Rejected"),   # already Approved
        (2, "Approved"),   # already Approved
        (3, "Approved"),   # already Rejected
        (4, "Approved"),   # already Cancelled
    ])
    def test_terminal_states_cannot_be_re_decided(self, client, request_id, decision):
        response = client.put(f"/api/unavailability-requests/{request_id}/review",
                              headers=MANAGER,
                              json={"decision": decision, "reviewed_by": "N"})
        assert response.status_code == 409

    @pytest.mark.parametrize("request_id", [2, 3, 4])
    def test_only_a_pending_request_can_be_withdrawn(self, client, request_id):
        staff_id = {2: 2, 3: 3, 4: 1}[request_id]
        response = client.put(
            f"/api/staff/{staff_id}/unavailability-requests/{request_id}/cancel",
            headers=_employee(staff_id))
        assert response.status_code == 409


class TestDecisionLeavesRosterAlone:
    """The feature records intent. It never reschedules anyone."""

    def test_approval_does_not_change_availability_status(self, client, stub_database):
        before = stub_database.staff[1]["availability_status"]
        client.put("/api/unavailability-requests/1/review", headers=MANAGER,
                   json={"decision": "Approved", "reviewed_by": "N"})
        assert stub_database.staff[1]["availability_status"] == before

    def test_approval_does_not_unassign_anyone(self, client, stub_database):
        before = {a["assignment_id"]: a["assignment_status"]
                  for a in stub_database.assignments.values()}
        client.put("/api/unavailability-requests/1/review", headers=MANAGER,
                   json={"decision": "Approved", "reviewed_by": "N"})
        after = {a["assignment_id"]: a["assignment_status"]
                 for a in stub_database.assignments.values()}
        assert after == before

    def test_approval_does_not_change_shifts(self, client, stub_database):
        before = dict(stub_database.shifts)
        client.put("/api/unavailability-requests/1/review", headers=MANAGER,
                   json={"decision": "Approved", "reviewed_by": "N"})
        assert stub_database.shifts == before

    def test_affected_shifts_are_derived_not_stored(self, client, stub_database):
        """Request 2 covers 24 August; staff 2 is not on that shift, but
        staff 1 is. The conflict list is computed from live assignments."""
        detail = client.get("/api/unavailability-requests/1",
                            headers=MANAGER).get_json()
        assert "affected_assignments" in detail
        # Stub request 1 covers 1–3 September; no assignment falls there.
        assert detail["affected_assignments"] == []

    def test_affected_shifts_reflect_current_assignments(self, client, stub_database):
        """Move the shift into the requested period and the conflict appears,
        without the request itself being touched."""
        stub_database.shifts[1]["shift_date"] = "2026-09-02"
        detail = client.get("/api/unavailability-requests/1",
                            headers=MANAGER).get_json()
        dates = [row["shift_date"] for row in detail["affected_assignments"]]
        assert dates == ["2026-09-02"]


class TestRequestAuthorization:
    """The frontend hides controls; this is what actually enforces the rule."""

    def test_employee_reads_own_requests(self, client):
        assert client.get("/api/staff/1/unavailability-requests",
                          headers=_employee(1)).status_code == 200

    def test_employee_cannot_read_another_employees_requests(self, client):
        assert client.get("/api/staff/2/unavailability-requests",
                          headers=_employee(1)).status_code == 403

    def test_employee_cannot_create_for_another_employee(self, client):
        response = client.post("/api/staff/2/unavailability-requests",
                               headers=_employee(1), json={
                                   "start_date": "2026-10-01",
                                   "end_date": "2026-10-02", "reason": "Personal"})
        assert response.status_code == 403

    def test_employee_cannot_cancel_another_employees_request(self, client):
        assert client.put("/api/staff/2/unavailability-requests/2/cancel",
                          headers=_employee(1)).status_code == 403

    def test_employee_cannot_read_the_review_queue(self, client):
        assert client.get("/api/unavailability-requests",
                          headers=_employee(1)).status_code == 403

    def test_employee_cannot_review_a_request(self, client):
        response = client.put("/api/unavailability-requests/1/review",
                              headers=_employee(1),
                              json={"decision": "Approved", "reviewed_by": "me"})
        assert response.status_code == 403

    def test_employee_cannot_open_the_staff_directory(self, client):
        assert client.get("/api/staff", headers=_employee(1)).status_code == 403

    def test_manager_reads_the_review_queue(self, client):
        assert client.get("/api/unavailability-requests",
                          headers=MANAGER).status_code == 200

    def test_manager_filters_the_queue_by_status(self, client):
        rows = client.get("/api/unavailability-requests?request_status=Pending",
                          headers=MANAGER).get_json()["requests"]
        assert [row["request_status"] for row in rows] == ["Pending"]

    def test_queue_carries_employee_context(self, client):
        """A reviewer must be able to see whose request it is."""
        row = client.get("/api/unavailability-requests",
                         headers=MANAGER).get_json()["requests"][0]
        assert row["staff_name"] and row["staff_role"] and row["staff_department"]

    def test_single_request_carries_the_same_context_as_the_listing(self, client):
        listed = client.get("/api/unavailability-requests",
                            headers=MANAGER).get_json()["requests"][0]
        single = client.get(f"/api/unavailability-requests/{listed['request_id']}",
                            headers=MANAGER).get_json()["request"]
        for field in ("staff_name", "staff_role", "staff_department"):
            assert single[field] == listed[field]


# ==========================================================================
# Scenario E — unexpected roster maintenance
# ==========================================================================
# The whole feature rests on one rule: changing operational availability
# records a judgement about a person, and never edits the roster. Everything
# below exists to keep that true.

class TestOperationalAvailabilityUpdate:
    @pytest.mark.parametrize("status", ["Unavailable", "On Leave", "Available"])
    def test_manager_sets_operational_status(self, client, status):
        response = client.put("/api/staff/1/availability", headers=MANAGER,
                              json={"availability_status": status})
        assert response.status_code == 200
        assert response.get_json()["staff"]["availability_status"] == status

    def test_invalid_status_is_rejected(self, client):
        response = client.put("/api/staff/1/availability", headers=MANAGER,
                              json={"availability_status": "On Holiday"})
        assert response.status_code == 400

    def test_unknown_staff_is_404(self, client):
        response = client.put("/api/staff/999/availability", headers=MANAGER,
                              json={"availability_status": "Unavailable"})
        assert response.status_code == 404

    def test_employee_cannot_set_operational_status(self, client):
        """Operational availability is a management judgement, not
        self-service — including on your own record."""
        response = client.put("/api/staff/1/availability", headers=_employee(1),
                              json={"availability_status": "Unavailable"})
        assert response.status_code == 403


class TestAvailabilityChangeLeavesRosterAlone:
    """The single most important guarantee in Scenario E."""

    def _set(self, client, status, staff_id=1):
        return client.put(f"/api/staff/{staff_id}/availability", headers=MANAGER,
                          json={"availability_status": status})

    def test_becoming_unavailable_does_not_unassign(self, client, stub_database):
        before = {a["assignment_id"]: dict(a)
                  for a in stub_database.assignments.values()}
        self._set(client, "Unavailable")
        after = {a["assignment_id"]: dict(a)
                 for a in stub_database.assignments.values()}
        assert after == before

    def test_becoming_unavailable_does_not_cancel_assignments(self, client,
                                                              stub_database):
        self._set(client, "Unavailable")
        statuses = [a["assignment_status"] for a in stub_database.assignments.values()]
        assert "Cancelled" not in statuses

    def test_going_on_leave_does_not_unassign(self, client, stub_database):
        before = len(stub_database.assignments)
        self._set(client, "On Leave")
        assert len(stub_database.assignments) == before

    def test_becoming_unavailable_does_not_create_a_replacement(self, client,
                                                                stub_database):
        before = set(stub_database.assignments)
        self._set(client, "Unavailable")
        assert set(stub_database.assignments) == before

    def test_returning_to_available_restores_nothing(self, client, stub_database):
        """Removing someone is a decision; undoing the status must not undo
        the decision."""
        client.put("/api/shifts/1/unassign", headers=MANAGER,
                   json={"staff_id": 1})
        after_removal = {a["assignment_id"]: dict(a)
                         for a in stub_database.assignments.values()}

        self._set(client, "Unavailable")
        self._set(client, "Available")

        assert {a["assignment_id"]: dict(a)
                for a in stub_database.assignments.values()} == after_removal

    def test_availability_change_does_not_touch_weekly_pattern(self, client,
                                                               stub_database):
        before = {k: dict(v) for k, v in stub_database.weekly.items()}
        self._set(client, "Unavailable")
        assert {k: dict(v) for k, v in stub_database.weekly.items()} == before

    def test_availability_change_does_not_create_a_request(self, client,
                                                           stub_database):
        """Scenario C's request entity is for planned, employee-initiated
        absence. An operational status change is neither."""
        before = set(stub_database.requests)
        self._set(client, "Unavailable")
        assert set(stub_database.requests) == before


class TestAssignmentPayloadCarriesAvailability:
    """The shift view cannot warn about a conflict it is never told about."""

    def test_assignment_listing_includes_operational_status(self, client):
        rows = client.get("/api/shifts/1/assignments",
                          headers=MANAGER).get_json()["assignments"]
        assert rows
        assert all("availability_status" in row for row in rows)

    def test_availability_change_is_visible_on_the_shift(self, client):
        client.put("/api/staff/1/availability", headers=MANAGER,
                   json={"availability_status": "Unavailable"})
        rows = client.get("/api/shifts/1/assignments",
                          headers=MANAGER).get_json()["assignments"]
        assigned = next(row for row in rows if row["staff_id"] == 1)
        assert assigned["availability_status"] == "Unavailable"
        # ...and they are still assigned.
        assert assigned["assignment_status"] == "Assigned"


class TestCoverageSemanticsUnchanged:
    """Coverage counts persisted active assignments. Scenario E deliberately
    did NOT change that; it makes the conflict visible instead. If this ever
    starts failing, coverage arithmetic was redefined without a decision."""

    def _coverage(self, client, shift_id=1):
        rows = client.get("/api/shifts/coverage", headers=MANAGER).get_json()["shifts"]
        return next(row for row in rows if row["shift_id"] == shift_id)

    def test_unavailable_assigned_staff_still_count_towards_coverage(self, client):
        before = self._coverage(client)
        client.put("/api/staff/1/availability", headers=MANAGER,
                   json={"availability_status": "Unavailable"})
        after = self._coverage(client)
        assert after["assigned_staff_count"] == before["assigned_staff_count"]
        assert after["shortfall"] == before["shortfall"]
        assert after["coverage_status"] == before["coverage_status"]

    def test_unassigning_opens_a_gap(self, client):
        before = self._coverage(client)
        client.put("/api/shifts/1/unassign", headers=MANAGER,
                   json={"staff_id": 1})
        after = self._coverage(client)
        assert after["assigned_staff_count"] == before["assigned_staff_count"] - 1
        assert after["shortfall"] == before["shortfall"] + 1

    def test_assigning_a_replacement_closes_the_gap(self, client):
        client.put("/api/shifts/1/unassign", headers=MANAGER,
                   json={"staff_id": 1})
        opened = self._coverage(client)
        client.post("/api/shifts/1/assign", headers=MANAGER, json={"staff_id": 2})
        closed = self._coverage(client)
        assert closed["shortfall"] == opened["shortfall"] - 1
