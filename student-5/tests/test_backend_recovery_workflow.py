"""API boundary tests for starting a shared Staffing Recovery workflow."""

from __future__ import annotations

import copy
import json

import pytest

from services import (mcp_client, multi_agent_client, rag_client,
                      recovery_mcp_service, recovery_policy_service,
                      recovery_service)


MANAGER = {"X-HOMS-Role": "Staff Manager"}
EMPLOYEE = {"X-HOMS-Role": "Employee", "X-HOMS-Staff-Id": "2"}
WORKFLOW_ID = "12345678-1234-4678-9234-567812345678"


def shared_result():
    return {
        "schema_version": "1.0",
        "workflow_id": WORKFLOW_ID,
        "status": "in_progress",
        "task": "shared task",
        "planner": None,
        "worker": None,
        "reviewer": None,
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


def install_rag_adapter(monkeypatch, *, result=None, error=None, calls=None):
    calls = calls if calls is not None else []

    class FakeRAGClient:
        def __init__(self, **configuration):
            calls.append(("rag_config", configuration))

        def ask(self, question):
            calls.append(("rag_ask", question))
            if error:
                raise error
            return result or {
                "schema_version": "1.0",
                "status": "answered",
                "answer": "Assignments require a manager decision [S1].",
                "confidence": "high",
                "citations": [{
                    "id": "S1",
                    "source": "student-5/staff-eligibility-and-assignments.md",
                    "title": "Staff Eligibility and Assignments",
                    "section": "Assignment creation boundary",
                    "score": 0.86,
                    "snippet": "Only a Staff Manager can create an assignment.",
                }],
                "reason": None,
            }

    import routes.recovery_routes as routes
    monkeypatch.setattr(routes, "StaffShiftRAGClient", FakeRAGClient)
    return calls


def mcp_result(evidence, shift_id=2):
    return {
        "schema_version": "1.0",
        "ok": True,
        "tool": mcp_client.STAFFING_RECOVERY_TOOL_NAME,
        "data": {
            "shift_id": shift_id,
            "evidence": copy.deepcopy(evidence),
            "source": mcp_client.STAFFING_RECOVERY_SOURCE,
            "mutation_allowed": False,
        },
        "error": None,
    }


def install_mcp_adapter(monkeypatch, *, result=None, error=None, calls=None):
    calls = calls if calls is not None else []

    class FakeMCPClient:
        def __init__(self, **configuration):
            calls.append(("mcp_config", configuration))

        def get_recovery_context(self, shift_id):
            calls.append(("mcp_call", shift_id))
            if error:
                raise error
            return result

    import routes.recovery_routes as routes
    monkeypatch.setattr(routes, "StaffingRecoveryMCPClient", FakeMCPClient)
    return calls


def post(client, shift_id=2, headers=MANAGER):
    return client.post(
        f"/api/shifts/{shift_id}/recovery-workflow", headers=headers)


def test_manager_starts_workflow_with_fresh_enriched_evidence(
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
    rag_calls = install_rag_adapter(monkeypatch)
    mcp_calls = install_mcp_adapter(
        monkeypatch, result=mcp_result(expected)
    )
    client.application.config["RAG_ENABLED"] = True
    client.application.config["MCP_ENABLED"] = True
    response = post(client)

    assert response.status_code == 202
    response_body = response.get_json()
    assert {
        key: value for key, value in response_body.items()
        if key != "submitted_evidence"
    } == shared_result()
    assert response_body["submitted_evidence"] == calls[1][2]
    assert response.headers["Location"] == (
        f"/api/recovery-workflows/{WORKFLOW_ID}"
    )
    assert response.headers["Retry-After"] == "1"
    assert builds == [2]
    assert rag_calls == [
        ("rag_config", {
            "enabled": True,
            "server_url": "http://127.0.0.1:8100",
            "timeout": 100.0,
        }),
        ("rag_ask", recovery_policy_service.POLICY_QUERY),
    ]
    assert calls[0] == ("config", {
        "base_url": "http://127.0.0.1:8200",
        "timeout": 600.0,
    })
    _, task, submitted = calls[1]
    assert task == routes.RECOVERY_WORKFLOW_TASK
    assert "human_approval constraint is mandatory" in task
    assert "exact top-level evidence category names" in task
    assert "MCP context only as a controlled read-only access record" in task
    assert expected == expected_before
    for field in recovery_policy_service.OPERATIONAL_FACT_FIELDS:
        assert submitted[field] == expected[field]
    assert submitted["evidence_type"] == "student5_staffing_recovery"
    assert submitted["unknowns"] == recovery_service.UNKNOWNS
    assert submitted["policy_context"]["retrieval_status"] == "answered"
    assert submitted["policy_context"]["citations"][0]["source"] == (
        "student-5/staff-eligibility-and-assignments.md"
    )
    assert submitted["evidence_separation"][
        "operational_rules_remain_authoritative"
    ] is True
    assert mcp_calls == [
        ("mcp_config", {
            "enabled": True,
            "server_url": "http://127.0.0.1:8000/mcp",
            "timeout": 15.0,
        }),
        ("mcp_call", 2),
    ]
    assert submitted["mcp_context"]["retrieval_status"] == "available"
    assert submitted["mcp_context"]["tool_name"] == (
        mcp_client.STAFFING_RECOVERY_TOOL_NAME
    )
    assert submitted["mcp_context"]["tool_input"] == {"shift_id": 2}
    assert submitted["mcp_context"]["tool_result"]["evidence"] == expected
    assert submitted["mcp_context"]["consistency_status"] == (
        "matched_authoritative_backend_evidence"
    )


def test_shared_workflow_identity_and_review_gate_are_preserved(client, monkeypatch):
    result = shared_result()
    install_adapter(monkeypatch, result=result)

    body = post(client).get_json()

    assert body["workflow_id"] == WORKFLOW_ID
    assert body["status"] == "in_progress"
    assert body["human_review"] is None
    assert body["final_result"] is None
    assert body["submitted_evidence"]["provenance"]["mutation_allowed"] is False


def test_privacy_safe_recovery_evidence_reaches_client(client, monkeypatch):
    calls = install_adapter(monkeypatch)
    assert post(client, 1).status_code == 202

    evidence = calls[1][2]
    serialised = json.dumps({
        field: evidence[field]
        for field in recovery_policy_service.OPERATIONAL_FACT_FIELDS
    })
    for private_value in (
        "Amara Okafor", "Daniel Reyes", "Mei Lin Tan", "Personal",
        "Vacation", "Study leave",
    ):
        assert private_value not in serialised
    assert '"name"' not in serialised
    assert '"notes"' not in serialised
    assert '"reason"' not in serialised
    assert evidence["provenance"]["mutation_allowed"] is False
    assert "staff-" not in evidence["policy_context"]["query"]
    assert "shift 1" not in evidence["policy_context"]["query"]


@pytest.mark.parametrize("failure,reason", [
    (rag_client.RAGUnavailable("private http://127.0.0.1 detail"), "rag_unavailable"),
    (rag_client.RAGTimeout(), "rag_timeout"),
    (rag_client.RAGInvalidResponse(), "rag_invalid_response"),
])
def test_rag_failure_degrades_but_workflow_still_reaches_multi_agent(
        client, monkeypatch, failure, reason):
    calls = install_adapter(monkeypatch)
    rag_calls = install_rag_adapter(monkeypatch, error=failure)
    client.application.config["RAG_ENABLED"] = True

    response = post(client)

    assert response.status_code == 202
    submitted = calls[-1][2]
    assert submitted["policy_context"]["retrieval_status"] == "unavailable"
    assert submitted["policy_context"]["reason"] == reason
    assert submitted["current_state"]["shift"]["shift_id"] == 2
    assert rag_calls[-1] == ("rag_ask", recovery_policy_service.POLICY_QUERY)
    assert "127.0.0.1" not in json.dumps(submitted["policy_context"])


def test_insufficient_rag_context_is_recorded_and_workflow_continues(
        client, monkeypatch):
    calls = install_adapter(monkeypatch)
    install_rag_adapter(monkeypatch, result={
        "schema_version": "1.0",
        "status": "insufficient_context",
        "answer": "The knowledge base does not contain enough relevant information.",
        "confidence": "none",
        "citations": [],
        "reason": "no_relevant_context",
    })
    client.application.config["RAG_ENABLED"] = True

    response = post(client)

    assert response.status_code == 202
    policy = calls[-1][2]["policy_context"]
    assert policy["retrieval_status"] == "insufficient_context"
    assert policy["reason"] == "no_relevant_context"
    assert policy["citations"] == []


@pytest.mark.parametrize("failure,reason", [
    (mcp_client.MCPDisabledError(), "mcp_disabled"),
    (mcp_client.MCPUnavailable("private http://127.0.0.1 detail"), "mcp_unavailable"),
    (mcp_client.MCPTimeout(), "mcp_timeout"),
    (mcp_client.MCPInvalidResponse(), "mcp_invalid_response"),
    (mcp_client.MCPToolFailure("upstream_invalid_response"), "mcp_invalid_response"),
])
def test_mcp_failure_degrades_but_rag_and_workflow_continue(
        client, monkeypatch, failure, reason):
    calls = install_adapter(monkeypatch)
    install_rag_adapter(monkeypatch)
    mcp_calls = install_mcp_adapter(monkeypatch, error=failure)
    client.application.config.update(RAG_ENABLED=True, MCP_ENABLED=True)

    response = post(client)

    assert response.status_code == 202
    submitted = calls[-1][2]
    assert submitted["policy_context"]["retrieval_status"] == "answered"
    assert submitted["mcp_context"]["retrieval_status"] == "unavailable"
    assert submitted["mcp_context"]["reason"] == reason
    assert submitted["current_state"]["shift"]["shift_id"] == 2
    assert mcp_calls[-1] == ("mcp_call", 2)
    assert "127.0.0.1" not in json.dumps(submitted["mcp_context"])


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


def test_cloud_multi_agent_switch_blocks_workflow_before_network(
        client, monkeypatch):
    calls = install_adapter(monkeypatch)
    client.application.config["MULTI_AGENT_ENABLED"] = False

    response = post(client)

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "multi_agent_unavailable",
        "message": "The shared Multi-Agent workflow service is disabled.",
    }
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
    expected_evidence = recovery_service.build_recovery_evidence(2)

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
    install_mcp_adapter(
        monkeypatch, result=mcp_result(expected_evidence)
    )
    client.application.config["MCP_ENABLED"] = True

    assert post(client).status_code == 202
    after = {field: getattr(stub_database, field) for field in state_fields}
    assert after == before


def test_api_index_lists_workflow_but_health_does_not_depend_on_it(client):
    assert "POST   /api/shifts/<shift_id>/recovery-workflow" in client.get(
        "/api").get_json()["endpoints"]["shifts"]
    health = client.get("/health")
    assert health.status_code == 200
    assert "multi_agent" not in health.get_json()
