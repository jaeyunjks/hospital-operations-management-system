"""Focused contract tests for the read-only Staffing Recovery evidence API."""

from __future__ import annotations

import copy
import json

from errors import DatabaseServiceError
from services import recovery_service


MANAGER = {"X-HOMS-Role": "Staff Manager"}
EMPLOYEE = {"X-HOMS-Role": "Employee", "X-HOMS-Staff-Id": "2"}


def _get(client, shift_id=2):
    return client.get(
        f"/api/shifts/{shift_id}/recovery-evidence", headers=MANAGER)


def _options_by_id(body):
    return {option["id"]: option for option in body["options"]}


class TestRecoveryEvidenceContract:
    def test_valid_manager_request_returns_generic_evidence(self, client):
        response = _get(client)

        assert response.status_code == 200
        body = response.get_json()
        assert body["schema_version"] == "1.0"
        assert body["evidence_type"] == "student5_staffing_recovery"
        assert body["evidence_mode"] == "live_api"
        assert set(body) == {
            "schema_version", "evidence_type", "evidence_mode",
            "current_state", "objective", "options", "constraints",
            "unknowns", "provenance",
        }

    def test_shift_identity_and_coverage_arithmetic_are_authoritative(self, client):
        body = _get(client, 1).get_json()

        assert body["current_state"]["shift"] == {
            "shift_id": 1,
            "department": "Emergency",
            "shift_date": "2026-08-24",
            "start_time": "07:00",
            "end_time": "15:00",
            "required_role": "Registered Nurse",
            "required_staff_count": 2,
            "shift_status": "Planned",
        }
        assert body["current_state"]["coverage"] == {
            "required_staff_count": 2,
            "assigned_staff_count": 1,
            "filled_staff_count": 1,
            "shortfall": 1,
            "surplus": 0,
            "coverage_status": "Understaffed",
        }
        assert body["objective"] == {
            "metric": "assigned_staff",
            "current": 1,
            "target": 2,
            "unit": "staff member",
            "staffing_gap": 1,
        }

    def test_current_assignments_are_privacy_safe(self, client):
        assignments = _get(client, 1).get_json()["current_state"][
            "current_assignments"]

        assert assignments == [{
            "staff_ref": "staff-1",
            "role": "Registered Nurse",
            "department": "Emergency",
            "availability_status": "Available",
            "assignment_status": "Assigned",
        }]

    def test_candidate_projection_preserves_eligibility_and_blockers(self, client):
        options = _options_by_id(_get(client).get_json())

        available = options["staff-2"]
        assert available["attributes"]["backend_eligible"] is True
        assert available["attributes"]["blocked_reason"] is None
        assert available["outcomes"] == {
            "metric_delta": 1,
            "projected_assigned_staff_count": 1,
            "projected_staffing_gap": 0,
        }

        blocked = options["staff-3"]
        assert blocked["attributes"]["backend_eligible"] is False
        assert blocked["attributes"]["blocked_reason"] == "On Leave"
        assert blocked["outcomes"]["metric_delta"] == 0
        assert "Operational availability status is On Leave." in blocked[
            "risk_indicators"]

    def test_schedule_conflict_is_projected_without_notes(self, client):
        client.post("/api/shifts/2/assign", json={"staff_id": 2}, headers=MANAGER)
        created = client.post("/api/shifts", headers=MANAGER, json={
            "department": "Surgery",
            "shift_date": "2026-08-25",
            "start_time": "12:00",
            "end_time": "20:00",
            "required_role": "Doctor",
            "required_staff_count": 1,
        }).get_json()["shift"]

        option = _options_by_id(
            _get(client, created["shift_id"]).get_json())["staff-2"]
        attributes = option["attributes"]
        assert attributes["backend_eligible"] is False
        assert attributes["conflicting_assignment"]["shift_id"] == 2
        assert attributes["conflicting_assignment"]["start_time"] == "08:00"
        assert attributes["current_assignments"][0]["shift_id"] == 2
        assert "An overlapping active assignment was identified." in option[
            "risk_indicators"]

    def test_weekly_availability_and_hours_are_context_not_invented_limits(self, client):
        body = _get(client, 1).get_json()
        option = _options_by_id(body)["staff-1"]

        assert option["attributes"]["weekly_availability_matches"] is True
        assert option["attributes"]["weekly_rostered_hours"] == 8.0
        assert any(
            constraint["id"] == "weekly_availability_review"
            and constraint["type"] == "policy"
            for constraint in body["constraints"]
        )
        assert not any("40" in json.dumps(item) for item in body["constraints"])

    def test_approved_unavailability_excludes_reason_and_notes(self, client):
        created = client.post("/api/shifts", headers=MANAGER, json={
            "department": "Surgery",
            "shift_date": "2026-08-24",
            "start_time": "08:00",
            "end_time": "16:00",
            "required_role": "Doctor",
            "required_staff_count": 1,
        }).get_json()["shift"]

        option = _options_by_id(
            _get(client, created["shift_id"]).get_json())["staff-2"]
        assert option["attributes"]["approved_unavailability"] == {
            "start_date": "2026-08-24",
            "end_date": "2026-08-24",
            "request_status": "Approved",
        }
        serialised = json.dumps(option)
        assert "Personal" not in serialised
        assert '"reason"' not in serialised
        assert '"notes"' not in serialised

    def test_unknowns_are_explicit_and_provenance_forbids_mutation(self, client):
        body = _get(client).get_json()

        assert body["unknowns"] == recovery_service.UNKNOWNS
        assert body["provenance"]["mutation_allowed"] is False
        assert {source["service"] for source in body["provenance"]["sources"]} == {
            "shift_service.get_shift",
            "coverage_service.shift_coverage",
            "assignment_service.list_shift_assignments",
            "eligibility_service.candidates_for_shift",
        }
        assert any(
            item["id"] == "human_approval" and item["type"] == "approval"
            for item in body["constraints"]
        )

    def test_payload_contains_no_staff_names_notes_or_absence_reasons(self, client):
        serialised = json.dumps(_get(client, 1).get_json())

        for private_value in (
            "Amara Okafor", "Daniel Reyes", "Mei Lin Tan", "Personal",
            "Vacation", "Study leave",
        ):
            assert private_value not in serialised
        assert '"name"' not in serialised
        assert '"notes"' not in serialised
        assert '"reason"' not in serialised

    def test_same_underlying_state_produces_identical_output(self, client):
        assert _get(client).get_json() == _get(client).get_json()


class TestRecoveryEvidenceSafetyAndErrors:
    def test_endpoint_does_not_call_any_database_mutation(self, client, stub_database,
                                                         monkeypatch):
        state_fields = (
            "staff", "shifts", "assignments", "weekly", "requests",
            "_next_shift_id", "_next_assignment_id", "_next_weekly_id",
            "_next_request_id",
        )
        before = copy.deepcopy({
            field: getattr(stub_database, field) for field in state_fields
        })

        def mutation_forbidden(*args, **kwargs):
            raise AssertionError("A read-only evidence endpoint attempted a mutation")

        for method in (
            "create_shift", "update_shift", "delete_shift",
            "create_assignment", "update_assignment", "delete_assignment",
            "update_staff", "replace_weekly_availability",
            "create_unavailability_request", "update_unavailability_request",
        ):
            monkeypatch.setattr(stub_database, method, mutation_forbidden)

        assert _get(client).status_code == 200
        after = {field: getattr(stub_database, field) for field in state_fields}
        assert after == before

    def test_requires_staff_manager_role(self, client):
        response = client.get(
            "/api/shifts/2/recovery-evidence", headers=EMPLOYEE)

        assert response.status_code == 403
        assert response.get_json()["error"] == "forbidden"

    def test_unknown_and_invalid_shift_ids_return_json_404(self, client):
        unknown = _get(client, 999)
        invalid = client.get(
            "/api/shifts/not-an-id/recovery-evidence", headers=MANAGER)

        assert unknown.status_code == 404
        assert unknown.get_json()["error"] == "not_found"
        assert invalid.status_code == 404
        assert invalid.get_json()["error"] == "not_found"

    def test_missing_coverage_fails_closed(self, client, monkeypatch):
        monkeypatch.setattr(
            recovery_service.coverage_service,
            "shift_coverage",
            lambda **kwargs: {"shifts": []},
        )

        response = _get(client)
        assert response.status_code == 503
        assert response.get_json()["error"] == "database_service_unavailable"

    def test_missing_candidate_data_fails_closed(self, client, monkeypatch):
        monkeypatch.setattr(
            recovery_service.eligibility_service,
            "candidates_for_shift",
            lambda shift_id: {"eligible_count": 0},
        )

        response = _get(client)
        assert response.status_code == 503
        assert response.get_json()["error"] == "database_service_unavailable"

    def test_downstream_error_uses_existing_service_error_contract(
            self, client, monkeypatch):
        def unavailable(shift_id):
            raise DatabaseServiceError("Database service unreachable.")

        monkeypatch.setattr(
            recovery_service.shift_service, "get_shift", unavailable)
        response = _get(client)

        assert response.status_code == 503
        assert response.get_json() == {
            "error": "database_service_unavailable",
            "message": "Database service unreachable.",
        }

    def test_unexpected_internal_failure_uses_json_500_contract(
            self, client, monkeypatch):
        def explode(shift_id):
            raise RuntimeError("internal detail must not leak")

        monkeypatch.setattr(
            recovery_service.eligibility_service, "candidates_for_shift", explode)
        response = _get(client)

        assert response.status_code == 500
        assert response.get_json() == {
            "error": "internal_error",
            "message": "An unexpected error occurred.",
        }
