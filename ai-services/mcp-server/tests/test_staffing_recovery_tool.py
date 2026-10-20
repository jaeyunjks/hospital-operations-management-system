"""Contract tests for the bounded Student 5 staffing-recovery MCP tool."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from jsonschema import validate
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as server_module
from tools import staffing_recovery


def evidence(shift_id=11):
    return {
        "schema_version": "1.0",
        "evidence_type": "student5_staffing_recovery",
        "evidence_mode": "live_api",
        "current_state": {
            "shift": {
                "shift_id": shift_id,
                "department": "Emergency",
                "shift_date": "2026-10-18",
                "start_time": "15:00",
                "end_time": "23:00",
                "required_role": "Registered Nurse",
                "required_staff_count": 2,
                "shift_status": "Planned",
            },
            "coverage": {
                "required_staff_count": 2,
                "assigned_staff_count": 1,
                "filled_staff_count": 1,
                "shortfall": 1,
                "surplus": 0,
                "coverage_status": "Understaffed",
            },
            "current_assignments": [{
                "staff_ref": "staff-1",
                "role": "Registered Nurse",
                "department": "Emergency",
                "availability_status": "Available",
                "assignment_status": "Assigned",
            }],
        },
        "objective": {
            "metric": "assigned_staff",
            "current": 1,
            "target": 2,
            "unit": "staff member",
            "staffing_gap": 1,
        },
        "options": [{
            "id": "staff-2",
            "label": "staff-2",
            "attributes": {
                "backend_eligible": True,
                "role": "Registered Nurse",
                "specialisation": "Triage",
                "department": "Emergency",
                "employment_status": "Full-Time",
                "availability_status": "Available",
                "department_match": True,
                "weekly_availability_matches": True,
                "weekly_rostered_hours": 8.0,
                "blocked_reason": None,
                "approved_unavailability": None,
                "current_assignments": [],
                "conflicting_assignment": None,
            },
            "outcomes": {
                "metric_delta": 1,
                "projected_assigned_staff_count": 2,
                "projected_staffing_gap": 0,
            },
            "risk_indicators": [],
        }],
        "constraints": [
            {
                "id": "backend_eligibility",
                "scope": "option",
                "type": "hard",
                "field": "backend_eligible",
                "operator": "equals",
                "value": True,
                "description": "The backend must mark the candidate eligible.",
            },
            {
                "id": "human_approval",
                "scope": "workflow",
                "type": "approval",
                "description": "Human approval is required before any change.",
            },
        ],
        "unknowns": ["Fatigue thresholds are not configured."],
        "provenance": {
            "sources": [{
                "endpoint": "GET /api/shifts/<shift_id>",
                "service": "shift_service.get_shift",
            }],
            "mutation_allowed": False,
        },
    }


@pytest.fixture
def mock_api(monkeypatch):
    def install(body=None, *, status=200, error=None, text=None, handler=None):
        requests = []

        def respond(request):
            requests.append(request)
            if error:
                raise error
            if handler:
                return handler(request)
            if text is not None:
                return httpx.Response(status, text=text)
            return httpx.Response(status, json=evidence() if body is None else body)

        transport = httpx.MockTransport(respond)
        monkeypatch.setattr(
            staffing_recovery,
            "_client",
            lambda timeout: httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ),
        )
        return requests

    return install


def call(arguments):
    async def invoke():
        async with Client(server_module.mcp_server) as client:
            return await client.call_tool(staffing_recovery.TOOL_NAME, arguments)

    result = asyncio.run(invoke())
    validate(result.structured_content, staffing_recovery.OUTPUT_SCHEMA)
    return result


def assert_error(result, code, reason=None):
    assert result.is_error is True
    assert result.structured_content["tool"] == staffing_recovery.TOOL_NAME
    assert result.structured_content["data"] is None
    assert result.structured_content["error"]["code"] == code
    if reason:
        assert result.structured_content["error"]["details"]["reason"] == reason


def test_registered_with_one_required_bounded_input():
    async def discover():
        async with Client(server_module.mcp_server) as client:
            return await client.list_tools()

    tools = {tool.name: tool for tool in asyncio.run(discover()).tools}
    registered = tools[staffing_recovery.TOOL_NAME]
    assert registered.input_schema == {
        "type": "object",
        "properties": {
            "shift_id": {
                "type": "integer",
                "minimum": 1,
                "description": "Required. Positive whole-number Student 5 shift id.",
            }
        },
        "required": ["shift_id"],
        "additionalProperties": False,
    }
    assert registered.output_schema == staffing_recovery.OUTPUT_SCHEMA


def test_valid_shift_returns_exact_privacy_safe_evidence(mock_api, monkeypatch):
    requests = mock_api()
    monkeypatch.setattr(
        server_module,
        "config",
        replace(server_module.config, student5_api_url="http://student5.example:5500/api"),
    )

    result = call({"shift_id": 11})

    assert result.is_error is False
    assert result.structured_content == {
        "schema_version": "1.0",
        "ok": True,
        "tool": staffing_recovery.TOOL_NAME,
        "data": {
            "shift_id": 11,
            "evidence": evidence(),
            "source": staffing_recovery.SOURCE,
            "mutation_allowed": False,
        },
        "error": None,
    }
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url == httpx.URL(
        "http://student5.example:5500/api/shifts/11/recovery-evidence"
    )
    assert requests[0].headers[staffing_recovery.ROLE_HEADER] == "Staff Manager"


@pytest.mark.parametrize(
    "arguments,reason",
    [
        ({}, "missing"),
        ({"shift_id": None}, "missing"),
        ({"shift_id": 0}, "not_positive"),
        ({"shift_id": -1}, "not_positive"),
        ({"shift_id": "11"}, "wrong_type"),
        ({"shift_id": 11.0}, "wrong_type"),
        ({"shift_id": True}, "wrong_type"),
    ],
)
def test_invalid_shift_id_never_calls_backend(arguments, reason, mock_api):
    requests = mock_api()
    assert_error(call(arguments), "invalid_input", reason)
    assert requests == []


def test_unexpected_arguments_never_call_backend(mock_api):
    requests = mock_api()
    assert_error(
        call({"shift_id": 11, "url": "http://attacker.invalid"}),
        "invalid_input",
        "unexpected_fields",
    )
    assert requests == []


def test_unknown_shift(mock_api):
    requests = mock_api(status=404, text="private not found detail")
    result = call({"shift_id": 999})
    assert_error(result, "not_found")
    assert len(requests) == 1
    assert "private" not in json.dumps(result.structured_content)


@pytest.mark.parametrize(
    "status,code",
    [
        (500, "upstream_unavailable"),
        (503, "upstream_unavailable"),
        (408, "upstream_timeout"),
        (504, "upstream_timeout"),
        (401, "upstream_invalid_response"),
        (403, "upstream_invalid_response"),
        (302, "upstream_invalid_response"),
    ],
)
def test_backend_http_failures_are_safe(status, code, mock_api):
    mock_api(status=status, text="private internal backend detail")
    result = call({"shift_id": 11})
    assert_error(result, code)
    assert "private" not in json.dumps(result.structured_content)


def test_backend_unavailable(mock_api):
    mock_api(error=httpx.ConnectError("private host address"))
    result = call({"shift_id": 11})
    assert_error(result, "upstream_unavailable")
    assert "private" not in json.dumps(result.structured_content)


def test_backend_timeout(mock_api):
    mock_api(error=httpx.ReadTimeout("private connection detail"))
    result = call({"shift_id": 11})
    assert_error(result, "upstream_timeout")
    assert "private" not in json.dumps(result.structured_content)


def test_total_operation_timeout(monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=evidence())

    monkeypatch.setattr(
        staffing_recovery,
        "_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(slow)),
    )
    monkeypatch.setattr(
        server_module,
        "config",
        replace(server_module.config, student5_api_timeout=0.1),
    )
    assert_error(call({"shift_id": 11}), "upstream_timeout")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update(schema_version="2.0"),
        lambda body: body["current_state"]["shift"].update(shift_id=12),
        lambda body: body["current_state"]["coverage"].update(shortfall=99),
        lambda body: body["objective"].update(current=99),
        lambda body: body["provenance"].update(mutation_allowed=True),
        lambda body: body["options"][0]["attributes"].update(name="Private Name"),
    ],
)
def test_malformed_or_expanded_backend_response_is_rejected(mutate, mock_api):
    body = copy.deepcopy(evidence())
    mutate(body)
    mock_api(body)
    assert_error(call({"shift_id": 11}), "upstream_invalid_response")


def test_tool_has_no_mutation_operation_or_caller_chosen_destination(mock_api):
    requests = mock_api()
    call({"shift_id": 11})
    assert [request.method for request in requests] == ["GET"]
    assert [request.url.path for request in requests] == [
        "/api/shifts/11/recovery-evidence"
    ]
    source = Path(staffing_recovery.__file__).read_text()
    for forbidden in (".post(", ".put(", ".patch(", ".delete("):
        assert forbidden not in source


def test_student5_upstream_config_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("HOMS_STUDENT5_API_URL", raising=False)
    monkeypatch.delenv("HOMS_STUDENT5_API_TIMEOUT", raising=False)
    default = server_module.load_config()
    assert default.student5_api_url == "http://127.0.0.1:5500/api"
    assert default.student5_api_timeout == 10.0

    monkeypatch.setenv("HOMS_STUDENT5_API_URL", "http://student5.local:5501/api/")
    monkeypatch.setenv("HOMS_STUDENT5_API_TIMEOUT", "2.5")
    configured = server_module.load_config()
    assert configured.student5_api_url == "http://student5.local:5501/api"
    assert configured.student5_api_timeout == 2.5


@pytest.mark.parametrize(
    "value",
    ["", "ftp://localhost/api", "http://user:secret@localhost/api", "http://localhost/api?x=1"],
)
def test_invalid_student5_upstream_url_is_rejected(value, monkeypatch):
    monkeypatch.setenv("HOMS_STUDENT5_API_URL", value)
    with pytest.raises(ValueError, match="HOMS_STUDENT5_API_URL"):
        server_module.load_config()


@pytest.mark.parametrize("value", ["0", "0.09", "31", "nan", "not-a-number"])
def test_invalid_student5_timeout_is_rejected(value, monkeypatch):
    monkeypatch.setenv("HOMS_STUDENT5_API_TIMEOUT", value)
    with pytest.raises(ValueError, match="HOMS_STUDENT5_API_TIMEOUT"):
        server_module.load_config()
