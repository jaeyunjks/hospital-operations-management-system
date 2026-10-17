"""Phase 3E tests for the fixed MCP staffing-recovery adapter and enrichment."""

from __future__ import annotations

import asyncio
import copy
import inspect
from types import SimpleNamespace

import pytest

from services import mcp_client, recovery_mcp_service


def operational_evidence(shift_id=11):
    return {
        "schema_version": "1.0",
        "evidence_type": "student5_staffing_recovery",
        "evidence_mode": "live_api",
        "current_state": {"shift": {"shift_id": shift_id}},
        "objective": {"metric": "assigned_staff", "current": 1, "target": 2},
        "options": [{
            "id": "staff-2",
            "attributes": {"backend_eligible": True},
        }],
        "constraints": [{"id": "human_approval", "type": "approval"}],
        "unknowns": ["Fatigue threshold is not configured."],
        "provenance": {"mutation_allowed": False},
    }


def success(shift_id=11, evidence=None):
    return {
        "schema_version": "1.0",
        "ok": True,
        "tool": mcp_client.STAFFING_RECOVERY_TOOL_NAME,
        "data": {
            "shift_id": shift_id,
            "evidence": evidence or operational_evidence(shift_id),
            "source": mcp_client.STAFFING_RECOVERY_SOURCE,
            "mutation_allowed": False,
        },
        "error": None,
    }


class FakeSDKClient:
    instances = []
    result = None
    enter_error = None
    delay = 0

    def __init__(self, server, **kwargs):
        self.server = server
        self.kwargs = kwargs
        self.calls = []
        self.closed = False
        self.__class__.instances.append(self)

    async def __aenter__(self):
        if self.enter_error:
            raise self.enter_error
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    FakeSDKClient.instances = []
    FakeSDKClient.result = SimpleNamespace(
        is_error=False, structured_content=success()
    )
    FakeSDKClient.enter_error = None
    FakeSDKClient.delay = 0
    monkeypatch.setattr(mcp_client, "Client", FakeSDKClient)


def adapter(**kwargs):
    return mcp_client.StaffingRecoveryMCPClient(
        enabled=True,
        server_url="http://127.0.0.1:8000/mcp",
        timeout=1,
        **kwargs,
    )


def test_client_calls_only_fixed_tool_with_exact_shift_id():
    result = adapter().get_recovery_context(11)
    sdk = FakeSDKClient.instances[0]

    assert sdk.server == "http://127.0.0.1:8000/mcp"
    assert sdk.calls[0][0:2] == (
        mcp_client.STAFFING_RECOVERY_TOOL_NAME,
        {"shift_id": 11},
    )
    assert result == success()
    assert sdk.closed is True
    assert list(inspect.signature(
        mcp_client.StaffingRecoveryMCPClient.get_recovery_context
    ).parameters) == ["self", "shift_id"]


def test_disabled_client_never_opens_mcp_session():
    client = mcp_client.StaffingRecoveryMCPClient(enabled=False)
    with pytest.raises(mcp_client.MCPDisabledError):
        client.get_recovery_context(11)
    assert FakeSDKClient.instances == []


@pytest.mark.parametrize("shift_id", [None, 0, -1, "11", 11.0, True])
def test_client_rejects_invalid_shift_before_network(shift_id):
    with pytest.raises(mcp_client.MCPInvalidResponse):
        adapter().get_recovery_context(shift_id)
    assert FakeSDKClient.instances == []


def test_tool_error_is_classified_without_detail_leak():
    failed = {
        "schema_version": "1.0",
        "ok": False,
        "tool": mcp_client.STAFFING_RECOVERY_TOOL_NAME,
        "data": None,
        "error": {"code": "upstream_timeout", "message": "safe", "details": {}},
    }
    FakeSDKClient.result = SimpleNamespace(is_error=True, structured_content=failed)
    with pytest.raises(mcp_client.MCPToolFailure) as caught:
        adapter().get_recovery_context(11)
    assert caught.value.code == "upstream_timeout"


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body.update(tool="arbitrary_tool"),
        lambda body: body["data"].update(shift_id=12),
        lambda body: body["data"].update(source="private-source"),
        lambda body: body["data"].update(mutation_allowed=True),
        lambda body: body["data"]["evidence"].update(evidence_type="other"),
        lambda body: body["data"]["evidence"]["provenance"].update(
            mutation_allowed=True
        ),
    ],
)
def test_client_rejects_malformed_or_wrong_tool_result(change):
    body = copy.deepcopy(success())
    change(body)
    FakeSDKClient.result = SimpleNamespace(is_error=False, structured_content=body)
    with pytest.raises(mcp_client.MCPInvalidResponse):
        adapter().get_recovery_context(11)


def test_client_classifies_unavailable_and_timeout():
    FakeSDKClient.enter_error = OSError("private internal endpoint")
    with pytest.raises(mcp_client.MCPUnavailable):
        adapter().get_recovery_context(11)

    FakeSDKClient.enter_error = None
    FakeSDKClient.delay = 0.05
    slow = mcp_client.StaffingRecoveryMCPClient(
        enabled=True,
        server_url="http://127.0.0.1:8000/mcp",
        timeout=0.001,
    )
    with pytest.raises(mcp_client.MCPTimeout):
        slow.get_recovery_context(11)


class FakeRecoveryClient:
    def __init__(self, *, result=None, error=None):
        self.result = result or success()
        self.error = error
        self.shift_ids = []

    def get_recovery_context(self, shift_id):
        self.shift_ids.append(shift_id)
        if self.error:
            raise self.error
        return self.result


def rag_enriched(operational):
    return {
        **copy.deepcopy(operational),
        "evidence_separation": {
            "operational_fact_fields": ["current_state", "options"],
            "policy_context_field": "policy_context",
            "policy_is_advisory": True,
            "operational_rules_remain_authoritative": True,
        },
        "policy_context": {
            "context_type": "rag_policy_guidance",
            "retrieval_status": "answered",
            "answer": "Human approval is required [S1].",
        },
    }


def test_successful_enrichment_preserves_operational_rag_and_mcp_provenance():
    operational = operational_evidence()
    before = rag_enriched(operational)
    client = FakeRecoveryClient(result=success(evidence=operational))

    enriched = recovery_mcp_service.enrich_recovery_evidence_with_mcp(
        before, operational, 11, client
    )

    assert before == rag_enriched(operational)
    assert client.shift_ids == [11]
    for field in operational:
        assert enriched[field] == operational[field]
    assert enriched["policy_context"] == before["policy_context"]
    assert enriched["mcp_context"] == {
        "context_type": "mcp_operational_access",
        "retrieval_status": "available",
        "tool_name": mcp_client.STAFFING_RECOVERY_TOOL_NAME,
        "tool_input": {"shift_id": 11},
        "tool_result": success()["data"],
        "source": mcp_client.STAFFING_RECOVERY_SOURCE,
        "error_code": None,
        "reason": None,
        "consistency_status": "matched_authoritative_backend_evidence",
        "limitations": recovery_mcp_service.MCP_LIMITATIONS,
    }
    assert enriched["evidence_separation"] == {
        **before["evidence_separation"],
        "mcp_context_field": "mcp_context",
        "mcp_is_controlled_operational_access": True,
        "mcp_cannot_override_operational_rules": True,
    }


def test_mcp_mismatch_cannot_change_backend_eligibility():
    operational = operational_evidence()
    changed = copy.deepcopy(operational)
    changed["options"][0]["attributes"]["backend_eligible"] = False

    enriched = recovery_mcp_service.enrich_recovery_evidence_with_mcp(
        rag_enriched(operational),
        operational,
        11,
        FakeRecoveryClient(result=success(evidence=changed)),
    )

    assert enriched["options"][0]["attributes"]["backend_eligible"] is True
    assert enriched["mcp_context"]["retrieval_status"] == "inconsistent"
    assert enriched["mcp_context"]["consistency_status"] == "mismatch"
    assert enriched["mcp_context"]["tool_result"] is None


@pytest.mark.parametrize(
    "failure,reason,error_code",
    [
        (mcp_client.MCPDisabledError(), "mcp_disabled", None),
        (mcp_client.MCPUnavailable(), "mcp_unavailable", None),
        (mcp_client.MCPTimeout(), "mcp_timeout", None),
        (mcp_client.MCPInvalidResponse(), "mcp_invalid_response", None),
        (
            mcp_client.MCPToolFailure("upstream_unavailable"),
            "mcp_unavailable",
            "upstream_unavailable",
        ),
        (mcp_client.MCPToolFailure("upstream_timeout"), "mcp_timeout", "upstream_timeout"),
        (
            mcp_client.MCPToolFailure("upstream_invalid_response"),
            "mcp_invalid_response",
            "upstream_invalid_response",
        ),
        (mcp_client.MCPToolFailure("not_found"), "mcp_tool_error", "not_found"),
    ],
)
def test_failure_degrades_explicitly_without_changing_other_evidence(
    failure, reason, error_code
):
    operational = operational_evidence()
    before = rag_enriched(operational)

    enriched = recovery_mcp_service.enrich_recovery_evidence_with_mcp(
        before,
        operational,
        11,
        FakeRecoveryClient(error=failure),
    )

    for field in operational:
        assert enriched[field] == operational[field]
    assert enriched["policy_context"] == before["policy_context"]
    assert enriched["mcp_context"]["retrieval_status"] == "unavailable"
    assert enriched["mcp_context"]["reason"] == reason
    assert enriched["mcp_context"]["error_code"] == error_code
    assert enriched["mcp_context"]["tool_result"] is None


def test_mcp_enrichment_has_no_assignment_or_database_dependency():
    module_names = set(vars(recovery_mcp_service))
    assert "assignment_service" not in module_names
    assert "database_client" not in module_names
