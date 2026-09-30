from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from jsonschema import validate
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as server_module
from tools import care_tasks

TASK_FIELDS = {"task_id", "description", "status", "due_at"}


def clinical_record(record_id, admission_id=7):
    return {
        "record_id": record_id,
        "admission_id": admission_id,
        "patient_id": 501,
        "doctor_id": 1,
        "assessment_notes": "PRIVATE assessment",
        "diagnosis_summary": "PRIVATE diagnosis",
        "care_plan": "PRIVATE plan",
        "status": "active",
        "updated_after_discharge": 0,
    }


def care_task(task_id, record_id, status, description=None, due_at=None):
    return {
        "task_id": task_id,
        "clinical_record_id": record_id,
        "doctor_id": 1,
        "assigned_nurse_id": 7,
        "task_description": description or f"Task {task_id}",
        "notes": f"PRIVATE note {task_id}",
        "status": status,
        "due_at": due_at,
        "completed_at": "2026-09-01T09:00:00Z" if status == "completed" else None,
        "cancelled_by": "nurse" if status == "cancelled" else None,
    }


def history(records=None, admission_id=7):
    return {
        "admission_id": admission_id,
        "clinical_records": [clinical_record(1), clinical_record(2)]
        if records is None
        else records,
    }


def task_list(tasks=None):
    if tasks is None:
        tasks = [
            care_task(4, 2, "acknowledged", "Change dressing", "2026-09-30T10:00:00"),
            care_task(1, 1, "pending", "Check observations"),
            care_task(2, 1, "completed"),
            care_task(3, 2, "cancelled"),
        ]
    return {"care_tasks": tasks}


def backend(request):
    """Fake Student 2 backend: the two GET routes the tool is allowed to use."""

    if request.url.path.startswith("/api/clinical-records/admission/"):
        return httpx.Response(200, json=history())
    if request.url.path == "/api/care-tasks/":
        return httpx.Response(200, json=task_list())
    return httpx.Response(404, text="PRIVATE unknown route")


@pytest.fixture
def mock_api(monkeypatch):
    """Mock the HTTP boundary, not the projection or MCP dispatcher."""

    def install(handler=backend, *, error=None):
        requests = []

        def respond(request):
            requests.append(request)
            if error:
                raise error
            return handler(request)

        transport = httpx.MockTransport(respond)
        monkeypatch.setattr(
            care_tasks,
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
            return await client.call_tool(care_tasks.TOOL_NAME, arguments)

    result = asyncio.run(invoke())
    validate(result.structured_content, care_tasks.OUTPUT_SCHEMA)
    return result


def assert_error(result, code, reason=None):
    assert result.is_error is True
    assert result.structured_content["tool"] == "homs_open_care_tasks"
    assert result.structured_content["ok"] is False
    assert result.structured_content["data"] is None
    assert result.structured_content["error"]["code"] == code
    if reason:
        assert result.structured_content["error"]["details"]["reason"] == reason


def test_registered_with_strict_input_schema():
    async def discover():
        async with Client(server_module.mcp_server) as client:
            return await client.list_tools()

    tools = {tool.name: tool for tool in asyncio.run(discover()).tools}
    schema = tools["homs_open_care_tasks"].input_schema
    assert list(schema["properties"]) == ["admission_id"]
    assert schema["additionalProperties"] is False


def test_returns_only_open_tasks_with_four_fields(mock_api):
    mock_api()
    result = call({"admission_id": 7})
    assert result.is_error is False
    assert result.structured_content == {
        "schema_version": "1.0",
        "ok": True,
        "tool": "homs_open_care_tasks",
        "data": {
            "admission_id": 7,
            "count": 2,
            "tasks": [
                {
                    "task_id": 1,
                    "description": "Check observations",
                    "status": "pending",
                    "due_at": None,
                },
                {
                    "task_id": 4,
                    "description": "Change dressing",
                    "status": "acknowledged",
                    "due_at": "2026-09-30T10:00:00",
                },
            ],
        },
        "error": None,
    }
    for task in result.structured_content["data"]["tasks"]:
        assert set(task) == TASK_FIELDS


def test_open_tasks_are_sorted_by_task_id(mock_api):
    mock_api()
    tasks = call({"admission_id": 7}).structured_content["data"]["tasks"]
    assert [task["task_id"] for task in tasks] == [1, 4]


def test_admission_with_no_open_tasks_is_a_valid_empty_result(mock_api):
    mock_api(
        lambda request: httpx.Response(200, json=history())
        if request.url.path.startswith("/api/clinical-records/")
        else httpx.Response(
            200,
            json=task_list([care_task(1, 1, "completed"), care_task(2, 2, "cancelled")]),
        )
    )
    result = call({"admission_id": 7})
    assert result.is_error is False
    assert result.structured_content["data"] == {
        "admission_id": 7,
        "count": 0,
        "tasks": [],
    }


def test_tasks_on_records_outside_the_admission_are_excluded(mock_api):
    mock_api(
        lambda request: httpx.Response(200, json=history([clinical_record(1)]))
        if request.url.path.startswith("/api/clinical-records/")
        else httpx.Response(
            200,
            json=task_list([care_task(1, 1, "pending"), care_task(2, 99, "pending")]),
        )
    )
    tasks = call({"admission_id": 7}).structured_content["data"]["tasks"]
    assert [task["task_id"] for task in tasks] == [1]


def test_notes_and_private_details_never_appear(mock_api):
    mock_api()
    result = call({"admission_id": 7})
    assert result.is_error is False
    payload = json.dumps(result.structured_content)
    text_content = "".join(block.text for block in result.content)
    for leaked in ("PRIVATE", "notes", "assigned_nurse_id", "doctor_id", "patient_id"):
        assert leaked not in payload
        assert leaked not in text_content


@pytest.mark.parametrize(
    "arguments,reason",
    [
        ({"admission_id": 0}, "not_positive"),
        ({"admission_id": -1}, "not_positive"),
        ({"admission_id": "seven"}, "wrong_type"),
        ({"admission_id": "7"}, "wrong_type"),
        ({}, "missing"),
        ({"admission_id": None}, "missing"),
        ({"admission_id": True}, "wrong_type"),
        ({"admission_id": False}, "wrong_type"),
        ({"admission_id": 7.0}, "wrong_type"),
    ],
)
def test_bad_input_is_invalid_input_and_never_calls_backend(arguments, reason, mock_api):
    requests = mock_api()
    assert_error(call(arguments), "invalid_input", reason)
    assert requests == []


def test_unexpected_fields_are_invalid_input(mock_api):
    requests = mock_api()
    assert_error(
        call({"admission_id": 7, "patient_id": 501}),
        "invalid_input",
        "unexpected_fields",
    )
    assert requests == []


def test_admission_with_no_records_is_not_found(mock_api):
    requests = mock_api(
        lambda request: httpx.Response(200, json=history(records=[]))
    )
    assert_error(call({"admission_id": 7}), "not_found")
    # Nothing to look up tasks for, so the care-task route is never called.
    assert len(requests) == 1
    assert requests[0].url.path == "/api/clinical-records/admission/7"


def test_backend_down_is_upstream_unavailable(mock_api):
    mock_api(error=httpx.ConnectError("private host address"))
    result = call({"admission_id": 7})
    assert_error(result, "upstream_unavailable", "unreachable")
    assert "private" not in json.dumps(result.structured_content)


def test_backend_timeout_is_upstream_unavailable(mock_api):
    mock_api(error=httpx.ReadTimeout("private connection detail"))
    result = call({"admission_id": 7})
    assert_error(result, "upstream_unavailable", "timeout")
    assert "private" not in json.dumps(result.structured_content)


def test_total_operation_timeout(monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.2)
        return backend(request)

    transport = httpx.MockTransport(slow)
    monkeypatch.setattr(
        care_tasks, "_client", lambda timeout: httpx.AsyncClient(transport=transport)
    )
    monkeypatch.setattr(care_tasks, "API_TIMEOUT", 0.1)
    assert_error(call({"admission_id": 7}), "upstream_unavailable", "timeout")


@pytest.mark.parametrize(
    "status,reason",
    [
        (500, "server_error"),
        (503, "server_error"),
        (504, "timeout"),
        (408, "timeout"),
        (401, "access_denied"),
        (403, "access_denied"),
        (404, "unexpected_status"),
        (302, "unexpected_status"),
    ],
)
def test_backend_http_failures(status, reason, mock_api):
    mock_api(lambda request: httpx.Response(status, text="private upstream error detail"))
    result = call({"admission_id": 7})
    assert_error(result, "upstream_unavailable", reason)
    assert "private" not in json.dumps(result.structured_content)


def test_care_task_route_failure_after_records_found(mock_api):
    def handler(request):
        if request.url.path.startswith("/api/clinical-records/"):
            return httpx.Response(200, json=history())
        return httpx.Response(502, text="private")

    mock_api(handler)
    assert_error(call({"admission_id": 7}), "upstream_unavailable", "server_error")


def test_redirect_is_not_followed(mock_api):
    requests = mock_api(
        lambda request: httpx.Response(
            302, headers={"Location": "http://other.example/private"}
        )
    )
    assert_error(call({"admission_id": 7}), "upstream_unavailable", "unexpected_status")
    assert len(requests) == 1


def test_non_json(mock_api):
    mock_api(lambda request: httpx.Response(200, text="not JSON; patient_id=501"))
    assert_error(call({"admission_id": 7}), "upstream_unavailable", "invalid_response")


@pytest.mark.parametrize(
    "records_body",
    [
        [],
        {},
        {"admission_id": 8, "clinical_records": [clinical_record(1, 8)]},
        {"admission_id": 7, "clinical_records": "none"},
        {"admission_id": 7, "clinical_records": [clinical_record(1, admission_id=8)]},
        {"admission_id": 7, "clinical_records": [{"admission_id": 7}]},
    ],
)
def test_invalid_record_history(records_body, mock_api):
    mock_api(lambda request: httpx.Response(200, json=records_body))
    assert_error(call({"admission_id": 7}), "upstream_unavailable", "invalid_response")


@pytest.mark.parametrize(
    "task_body",
    [
        [],
        {},
        {"care_tasks": "none"},
        {"care_tasks": [{**care_task(1, 1, "pending"), "task_description": ""}]},
        {"care_tasks": [{**care_task(1, 1, "pending"), "task_description": "  "}]},
        {"care_tasks": [{**care_task(1, 1, "pending"), "task_id": "1"}]},
        {"care_tasks": [{**care_task(1, 1, "pending"), "due_at": 20260930}]},
    ],
)
def test_invalid_task_list(task_body, mock_api):
    def handler(request):
        if request.url.path.startswith("/api/clinical-records/"):
            return httpx.Response(200, json=history())
        return httpx.Response(200, json=task_body)

    mock_api(handler)
    assert_error(call({"admission_id": 7}), "upstream_unavailable", "invalid_response")


def test_only_get_requests_with_fixed_role_are_made(mock_api):
    requests = mock_api()
    assert call({"admission_id": 7}).is_error is False
    assert [request.method for request in requests] == ["GET", "GET"]
    assert str(requests[0].url).endswith("/api/clinical-records/admission/7")
    assert requests[1].url.path == "/api/care-tasks/"
    assert requests[1].url.params["admission_id"] == "7"
    for request in requests:
        assert request.headers["X-User-Role"] == "doctor"
        assert request.content == b""


@pytest.mark.parametrize(
    "scenario",
    ["success", "not_found", "backend_down", "server_error", "invalid_json"],
)
def test_every_outcome_uses_get_only(scenario, mock_api):
    handlers = {
        "success": backend,
        "not_found": lambda request: httpx.Response(200, json=history(records=[])),
        "backend_down": None,
        "server_error": lambda request: httpx.Response(500),
        "invalid_json": lambda request: httpx.Response(200, text="{"),
    }
    if scenario == "backend_down":
        requests = mock_api(error=httpx.ConnectError("down"))
    else:
        requests = mock_api(handlers[scenario])
    call({"admission_id": 7})
    assert requests
    assert {request.method for request in requests} == {"GET"}
