"""API boundary tests for starting a shared Staffing Recovery workflow."""

from __future__ import annotations

import copy
import json

import pytest

from services import multi_agent_client, recovery_service


MANAGER = {"X-HOMS-Role": "Staff Manager"}
EMPLOYEE = {"X-HOMS-Role": "Employee", "X-HOMS-Staff-Id": "2"}
WORKFLOW_ID = "12345678-1234-4678-9234-567812345678"


def shared_result():
    stage = {"output": {"complete": True}, "execution": {"provider": "test"}}
    return {
        "schema_version": "1.0",
        "workflow_id": WORKFLOW_ID,
        "status": "awaiting_human_review",
        "task": "shared task",
        "planner": copy.deepcopy(stage),
        "worker": copy.deepcopy(stage),
        "reviewer": copy.deepcopy(stage),
        "human_review": None,
        "final_result": None,
    }


def install_adapter(monkeypatch, *, result=None, error=None, calls=None):
    calls = calls if calls is not None else []

    class FakeClient:
        def __init__(self, **configuration):
            calls.append(("config", configuration))

        def start_workflow(self, task, evidence):
            calls.append(("start", task, evidence))
            if error:
                raise error
            return result or shared_result()

    import routes.recovery_routes as routes
    monkeypatch.setattr(routes, "MultiAgentClient", FakeClient)
    return calls


def post(client, shift_id=2, headers=MANAGER):
    return client.post(
        f"/api/shifts/{shift_id}/recovery-workflow", headers=headers)


def test_manager_starts_workflow_with_fresh_unchanged_evidence(
        client, monkeypatch):
    import routes.recovery_routes as routes

    expected = recovery_service.build_recovery_evidence(2)
    expected_before = copy.deepcopy(expected)
    builds = []

    def build(shift_id):
        builds.append(shift_id)
        return expected

    monkeypatch.setattr(routes.recovery_service, "build_recovery_evidence", build)
    calls = install_adapter(monkeypatch)
    response = post(client)

    assert response.status_code == 201
    assert response.get_json() == shared_result()
    assert builds == [2]
    assert calls[0] == ("config", {
        "base_url": "http://127.0.0.1:8200",
        "timeout": 600.0,
    })
    _, task, submitted = calls[1]
    assert task == routes.RECOVERY_WORKFLOW_TASK
    assert task == (
        "Review the selected hospital shift for staffing coverage problems. "
        "Evaluate the supplied candidates and operational constraints. "
        "Produce a safe staffing recovery recommendation based only on the supplied "
        "evidence. Identify risks, limitations, and missing information. "
        "Do not execute any staffing assignment or operational mutation."
    )
    assert submitted is expected
    assert submitted == expected_before
    assert submitted["evidence_type"] == "student5_staffing_recovery"
    assert submitted["unknowns"] == recovery_service.UNKNOWNS


def test_shared_workflow_identity_and_review_gate_are_preserved(client, monkeypatch):
    result = shared_result()
    install_adapter(monkeypatch, result=result)

    body = post(client).get_json()

    assert body["workflow_id"] == WORKFLOW_ID
    assert body["status"] == "awaiting_human_review"
    assert body["human_review"] is None
    assert body["final_result"] is None


def test_privacy_safe_recovery_evidence_reaches_client(client, monkeypatch):
    calls = install_adapter(monkeypatch)
    assert post(client, 1).status_code == 201

    evidence = calls[1][2]
    serialised = json.dumps(evidence)
    for private_value in (
        "Amara Okafor", "Daniel Reyes", "Mei Lin Tan", "Personal",
        "Vacation", "Study leave",
    ):
        assert private_value not in serialised
    assert '"name"' not in serialised
    assert '"notes"' not in serialised
    assert '"reason"' not in serialised
    assert evidence["provenance"]["mutation_allowed"] is False


def test_manager_guard_runs_before_evidence_or_shared_client(client, monkeypatch):
    import routes.recovery_routes as routes

    calls = []
    monkeypatch.setattr(
        routes.recovery_service,
        "build_recovery_evidence",
        lambda shift_id: calls.append(("evidence", shift_id)),
    )
    install_adapter(monkeypatch, calls=calls)

    response = post(client, headers=EMPLOYEE)

    assert response.status_code == 403
    assert response.get_json()["error"] == "forbidden"
    assert calls == []


def test_unknown_and_invalid_shift_ids_do_not_call_shared_service(
        client, monkeypatch):
    calls = install_adapter(monkeypatch)

    unknown = post(client, 999)
    invalid = client.post(
        "/api/shifts/not-an-id/recovery-workflow", headers=MANAGER)

    assert unknown.status_code == 404
    assert invalid.status_code == 404
    assert calls == []


@pytest.mark.parametrize("failure,status,code", [
    (multi_agent_client.MultiAgentUnavailable(), 503, "multi_agent_unavailable"),
    (multi_agent_client.MultiAgentTimeout(), 504, "multi_agent_timeout"),
    (multi_agent_client.MultiAgentHTTPError(), 502, "multi_agent_upstream_error"),
    (multi_agent_client.MultiAgentInvalidResponse(), 502,
     "multi_agent_invalid_response"),
])
def test_shared_failures_are_safely_mapped(
        client, monkeypatch, failure, status, code):
    install_adapter(monkeypatch, error=failure)

    response = post(client)

    assert response.status_code == status
    assert response.get_json()["error"] == code
    text = response.get_data(as_text=True)
    assert "127.0.0.1" not in text
    assert "Traceback" not in text


def test_starting_workflow_performs_no_student5_mutation(
        client, stub_database, monkeypatch):
    state_fields = (
        "staff", "shifts", "assignments", "weekly", "requests",
        "_next_shift_id", "_next_assignment_id", "_next_weekly_id",
        "_next_request_id",
    )
    before = copy.deepcopy({
        field: getattr(stub_database, field) for field in state_fields
    })

    def mutation_forbidden(*args, **kwargs):
        raise AssertionError("workflow start attempted a Student 5 mutation")

    for method in (
        "create_shift", "update_shift", "delete_shift",
        "create_assignment", "update_assignment", "delete_assignment",
        "update_staff", "replace_weekly_availability",
        "create_unavailability_request", "update_unavailability_request",
    ):
        monkeypatch.setattr(stub_database, method, mutation_forbidden)
    install_adapter(monkeypatch)

    assert post(client).status_code == 201
    after = {field: getattr(stub_database, field) for field in state_fields}
    assert after == before


def test_api_index_lists_workflow_but_health_does_not_depend_on_it(client):
    assert "POST   /api/shifts/<shift_id>/recovery-workflow" in client.get(
        "/api").get_json()["endpoints"]["shifts"]
    health = client.get("/health")
    assert health.status_code == 200
    assert "multi_agent" not in health.get_json()
