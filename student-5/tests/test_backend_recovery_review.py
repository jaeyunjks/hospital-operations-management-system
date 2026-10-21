"""Phase 3C API tests for workflow retrieval and advisory Human Review."""

from __future__ import annotations

import copy

import pytest

from services import multi_agent_client


MANAGER = {"X-HOMS-Role": "Staff Manager"}
EMPLOYEE = {"X-HOMS-Role": "Employee", "X-HOMS-Staff-Id": "2"}
WORKFLOW_ID = "12345678-1234-4678-9234-567812345678"


def shared_result(status="awaiting_human_review", decision=None, comment=""):
    stage = {
        "started_at": "2026-10-17T01:00:00Z",
        "completed_at": "2026-10-17T01:00:01Z",
        "output": {"complete": True},
        "execution": {"provider": "test"},
    }
    complete = status != "awaiting_human_review"
    return {
        "schema_version": "1.0",
        "workflow_id": WORKFLOW_ID,
        "status": status,
        "task": "Review the selected shift.",
        "planner": copy.deepcopy(stage),
        "worker": copy.deepcopy(stage),
        "reviewer": copy.deepcopy(stage),
        "human_review": ({
            "decision": decision,
            "comments": comment,
            "timestamp": "2026-10-17T01:01:00Z",
        } if complete else None),
        "final_result": ({
            "status": status,
            "human_decision": decision,
            "human_comments_or_correction": comment,
            "mutation_executed": False,
        } if complete else None),
    }


def install_adapter(monkeypatch, *, get_result=None, review_result=None,
                    list_result=None, get_error=None, review_error=None,
                    list_error=None, calls=None):
    calls = calls if calls is not None else []

    class FakeClient:
        def __init__(self, **configuration):
            calls.append(("config", configuration))

        def get_workflow(self, workflow_id):
            calls.append(("get", workflow_id))
            if get_error:
                raise get_error
            return get_result or shared_result()

        def list_workflows(self, evidence_type, *, evidence_path,
                           evidence_value, limit):
            calls.append((
                "list", evidence_type, evidence_path, evidence_value, limit))
            if list_error:
                raise list_error
            return list_result or {
                "schema_version": "1.0", "count": 0, "workflows": [],
            }

        def submit_review(self, workflow_id, decision, comment):
            calls.append(("review", workflow_id, decision, comment))
            if review_error:
                raise review_error
            return review_result or shared_result(
                status={
                    "approve": "approved",
                    "correct": "corrected",
                    "reject": "rejected",
                    "partially_accept": "partially_accepted",
                }[decision],
                decision=decision,
                comment=comment,
            )

    import routes.recovery_workflow_routes as routes
    monkeypatch.setattr(routes, "MultiAgentClient", FakeClient)
    return calls


def get(client, workflow_id=WORKFLOW_ID, headers=MANAGER):
    return client.get(
        f"/api/recovery-workflows/{workflow_id}", headers=headers
    )


def review(client, payload, workflow_id=WORKFLOW_ID, headers=MANAGER):
    return client.post(
        f"/api/recovery-workflows/{workflow_id}/review",
        headers=headers,
        json=payload,
    )


def history(client, shift_id=11, headers=MANAGER):
    return client.get(
        f"/api/recovery-workflows?shift_id={shift_id}", headers=headers
    )


def test_manager_gets_pending_workflow_with_all_agent_state(client, monkeypatch):
    calls = install_adapter(monkeypatch)

    response = get(client)

    assert response.status_code == 200
    body = response.get_json()
    assert body == shared_result()
    assert body["workflow_id"] == WORKFLOW_ID
    assert body["status"] == "awaiting_human_review"
    assert body["human_review"] is None
    assert body["final_result"] is None
    assert all(body[role]["output"]["complete"] for role in (
        "planner", "worker", "reviewer"
    ))
    assert calls == [
        ("config", {
            "base_url": "http://127.0.0.1:8200",
            "timeout": 600.0,
        }),
        ("get", WORKFLOW_ID),
    ]


def test_manager_history_filters_persisted_audits_to_selected_shift(
        client, monkeypatch):
    calls = install_adapter(monkeypatch, list_result={
        "schema_version": "1.0", "count": 1, "workflows": [
            {
                "workflow_id": WORKFLOW_ID,
                "status": "awaiting_human_review",
                "started_at": "2026-10-20T01:00:00Z",
                "human_review": None,
            },
        ],
    })

    response = history(client)

    assert response.status_code == 200
    assert response.get_json() == {
        "count": 1,
        "workflows": [{
            "workflow_id": WORKFLOW_ID,
            "shift_id": 11,
            "started_at": "2026-10-20T01:00:00Z",
            "status": "awaiting_human_review",
            "human_decision": None,
        }],
    }
    assert calls[-1] == (
        "list", "student5_staffing_recovery",
        "current_state.shift.shift_id", "11", 10,
    )


def test_employee_history_is_forbidden_before_network(client, monkeypatch):
    calls = install_adapter(monkeypatch)
    response = history(client, headers=EMPLOYEE)
    assert response.status_code == 403
    assert calls == []


@pytest.mark.parametrize("method", ["get", "review"])
def test_employee_is_forbidden_before_client_or_network(
        client, monkeypatch, method):
    calls = install_adapter(monkeypatch)

    response = (
        get(client, headers=EMPLOYEE)
        if method == "get"
        else review(client, {"decision": "approve"}, headers=EMPLOYEE)
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "forbidden"
    assert calls == []


@pytest.mark.parametrize("method", ["get", "review"])
def test_invalid_workflow_uuid_is_a_safe_validation_error(
        client, monkeypatch, method):
    error = multi_agent_client.MultiAgentInputError()
    calls = install_adapter(
        monkeypatch,
        get_error=error,
        review_error=error,
    )

    response = (
        get(client, "not-a-uuid")
        if method == "get"
        else review(client, {"decision": "approve"}, "not-a-uuid")
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"
    assert "Traceback" not in response.get_data(as_text=True)
    assert calls[-1][0] == method


@pytest.mark.parametrize("method", ["get", "review"])
def test_missing_workflow_maps_to_not_found(client, monkeypatch, method):
    install_adapter(
        monkeypatch,
        get_error=multi_agent_client.MultiAgentNotFound(),
        review_error=multi_agent_client.MultiAgentNotFound(),
    )

    response = (
        get(client)
        if method == "get"
        else review(client, {"decision": "approve"})
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "not_found", "message": "Workflow not found."
    }


@pytest.mark.parametrize("failure,status,code", [
    (multi_agent_client.MultiAgentUnavailable(), 503, "multi_agent_unavailable"),
    (multi_agent_client.MultiAgentTimeout(), 504, "multi_agent_timeout"),
    (multi_agent_client.MultiAgentHTTPError(), 502, "multi_agent_upstream_error"),
    (multi_agent_client.MultiAgentInvalidResponse(), 502,
     "multi_agent_invalid_response"),
])
@pytest.mark.parametrize("method", ["get", "review"])
def test_upstream_failures_are_safely_mapped(
        client, monkeypatch, failure, status, code, method):
    install_adapter(
        monkeypatch,
        get_error=failure,
        review_error=failure,
    )

    response = (
        get(client)
        if method == "get"
        else review(client, {"decision": "approve"})
    )

    assert response.status_code == status
    assert response.get_json()["error"] == code
    text = response.get_data(as_text=True)
    assert "127.0.0.1" not in text
    assert "private network" not in text
    assert "Traceback" not in text


@pytest.mark.parametrize("decision,expected_status,comment", [
    ("approve", "approved", "Accepted as advice."),
    ("correct", "corrected", "Use a different candidate."),
    ("reject", "rejected", "Risk is unresolved."),
    ("partially_accept", "partially_accepted", "Accept coverage finding only."),
])
def test_manager_forwards_each_exact_shared_review_decision(
        client, monkeypatch, decision, expected_status, comment):
    result = shared_result(expected_status, decision, comment)
    calls = install_adapter(monkeypatch, review_result=result)

    response = review(client, {"decision": decision, "comment": comment})

    assert response.status_code == 200
    assert response.get_json() == result
    assert response.get_json()["workflow_id"] == WORKFLOW_ID
    assert response.get_json()["final_result"]["mutation_executed"] is False
    assert calls[-1] == ("review", WORKFLOW_ID, decision, comment)


@pytest.mark.parametrize("payload", [
    None,
    [],
    {},
    {"comment": "Missing decision."},
    {"decision": "Approve"},
    {"decision": "partial"},
    {"decision": "approve", "comment": 5},
    {"decision": "correct", "comment": "   "},
    {"decision": "approve", "extra": True},
    {"decision": "approve", "comment": "x" * 10_001},
])
def test_malformed_review_body_is_rejected_before_client(
        client, monkeypatch, payload):
    calls = install_adapter(monkeypatch)
    response = client.post(
        f"/api/recovery-workflows/{WORKFLOW_ID}/review",
        headers=MANAGER,
        json=payload,
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"
    assert calls == []


def test_review_lifecycle_conflict_is_safe(client, monkeypatch):
    install_adapter(
        monkeypatch, review_error=multi_agent_client.MultiAgentConflict()
    )

    response = review(client, {"decision": "approve"})

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "conflict",
        "message": "Workflow is not awaiting Human Review.",
    }


@pytest.mark.parametrize("method", ["get", "review"])
def test_status_and_review_routes_cannot_mutate_student5_state(
        client, stub_database, monkeypatch, method):
    fields = (
        "staff", "shifts", "assignments", "weekly", "requests",
        "_next_shift_id", "_next_assignment_id", "_next_weekly_id",
        "_next_request_id",
    )
    before = copy.deepcopy({
        field: getattr(stub_database, field) for field in fields
    })

    def mutation_forbidden(*args, **kwargs):
        raise AssertionError("workflow status/review attempted a Student 5 mutation")

    for name in (
        "create_shift", "update_shift", "delete_shift",
        "create_assignment", "update_assignment", "delete_assignment",
        "update_staff", "replace_weekly_availability",
        "create_unavailability_request", "update_unavailability_request",
    ):
        monkeypatch.setattr(stub_database, name, mutation_forbidden)
    install_adapter(monkeypatch)

    response = (
        get(client)
        if method == "get"
        else review(client, {"decision": "reject", "comment": "Advisory only."})
    )

    assert response.status_code == 200
    after = {field: getattr(stub_database, field) for field in fields}
    assert after == before


def test_api_index_lists_recovery_lifecycle_and_history_routes(client):
    routes = client.get("/api").get_json()["endpoints"]["recovery_workflows"]
    assert routes == [
        "GET    /api/recovery-workflows?shift_id=<shift_id>",
        "GET    /api/recovery-workflows/<workflow_id>",
        "POST   /api/recovery-workflows/<workflow_id>/review",
    ]
