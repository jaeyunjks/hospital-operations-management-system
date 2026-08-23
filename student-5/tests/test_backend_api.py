"""API tests for the Student 5 backend/API microservice.

Covers every endpoint required by prompt artefact S5-BE-001, plus validation
and error handling. Run with:  pytest student-5/tests -v
"""

from __future__ import annotations


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
