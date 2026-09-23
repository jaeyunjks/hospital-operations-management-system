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
from tools import ward_occupancy


def snapshot():
    return {
        "success": True,
        "error": None,
        "data": {
            "wards": [
                {
                    "ward": "Emergency",
                    "total_beds": 3,
                    "occupied": 1,
                    "available": 2,
                    "reserved": 0,
                    "maintenance": 0,
                    "monitored_beds": 3,
                    "occupancy_pct": 33.3,
                    "care_categories": ["Short-term"],
                },
                {
                    "ward": "Critical Care",
                    "total_beds": 4,
                    "occupied": 2,
                    "available": 1,
                    "reserved": 1,
                    "maintenance": 0,
                    "monitored_beds": 4,
                    "occupancy_pct": 50.0,
                    "care_categories": ["Short-term"],
                },
            ],
            "totals": {
                "total_beds": 7,
                "occupied": 3,
                "available": 3,
                "reserved": 1,
                "maintenance": 0,
                "occupancy_pct": 42.9,
            },
        },
    }


def single_ward():
    body = snapshot()
    body["data"]["wards"] = body["data"]["wards"][:1]
    row = body["data"]["wards"][0]
    body["data"]["totals"] = {
        key: row[key] for key in (*ward_occupancy.COUNT_FIELDS, "occupancy_pct")
    }
    return body


def empty_snapshot():
    return {
        "success": True,
        "error": None,
        "data": {
            "wards": [],
            "totals": {
                **dict.fromkeys(ward_occupancy.COUNT_FIELDS, 0),
                "occupancy_pct": 0.0,
            },
        },
    }


@pytest.fixture
def mock_api(monkeypatch):
    """Mock the HTTP boundary, not the projection or MCP dispatcher."""

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
            return httpx.Response(status, json=snapshot() if body is None else body)

        transport = httpx.MockTransport(respond)
        monkeypatch.setattr(
            ward_occupancy,
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
            return await client.call_tool(ward_occupancy.TOOL_NAME, arguments)

    result = asyncio.run(invoke())
    validate(result.structured_content, ward_occupancy.OUTPUT_SCHEMA)
    return result


def assert_error(result, code, reason=None):
    assert result.is_error is True
    assert result.structured_content["tool"] == "homs_ward_occupancy_status"
    assert result.structured_content["ok"] is False
    assert result.structured_content["data"] is None
    assert result.structured_content["error"]["code"] == code
    if reason:
        assert result.structured_content["error"]["details"]["reason"] == reason


@pytest.mark.parametrize("arguments", [{}, {"ward": None}])
def test_all_wards(arguments, mock_api):
    requests = mock_api()
    result = call(arguments)
    assert result.is_error is False
    assert result.structured_content == {
        "schema_version": "1.0",
        "ok": True,
        "tool": "homs_ward_occupancy_status",
        "data": {
            "requested_ward": None,
            **snapshot()["data"],
            "source": "student-4-room-bed-api",
        },
        "error": None,
    }
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/api/wards/occupancy"
    assert not requests[0].url.query


def test_exact_ward_and_configured_url(mock_api, monkeypatch):
    requests = mock_api(single_ward())
    monkeypatch.setattr(
        server_module,
        "config",
        replace(
            server_module.config, student4_api_url="http://student4.example:5400/api"
        ),
    )
    result = call({"ward": "Emergency"})
    assert result.is_error is False
    assert result.structured_content["data"]["requested_ward"] == "Emergency"
    assert result.structured_content["data"]["wards"] == single_ward()["data"]["wards"]
    assert (
        str(requests[0].url)
        == "http://student4.example:5400/api/wards/occupancy?ward=Emergency"
    )


@pytest.mark.parametrize(
    "ward,reason",
    [
        ("", "blank"),
        (" \t\n", "blank"),
        (42, "wrong_type"),
        (False, "wrong_type"),
        ([], "wrong_type"),
        ({}, "wrong_type"),
        ("x" * 201, "too_long"),
    ],
)
def test_input_validation_does_not_call_student4(ward, reason, mock_api):
    requests = mock_api()
    assert_error(call({"ward": ward}), "validation_error", reason)
    assert requests == []


def test_unexpected_fields(mock_api):
    requests = mock_api()
    assert_error(
        call({"ward": None, "department": "Emergency"}),
        "validation_error",
        "unexpected_fields",
    )
    assert requests == []


def test_maximum_length_is_accepted(mock_api):
    body = single_ward()
    body["data"]["wards"][0]["ward"] = "x" * 200
    mock_api(body)
    assert call({"ward": "x" * 200}).is_error is False


@pytest.mark.parametrize(
    "ward", ["Unknown", "Intensive Care", "emergency", " Emergency "]
)
def test_unknown_ward_never_maps_or_normalises(ward, mock_api):
    requests = mock_api(empty_snapshot())
    assert_error(call({"ward": ward}), "WARD_NOT_FOUND")
    assert requests[0].url.params["ward"] == ward


def test_empty_all_wards_is_valid(mock_api):
    mock_api(empty_snapshot())
    assert call({}).is_error is False


def test_student4_timeout(mock_api):
    mock_api(error=httpx.ReadTimeout("private internal connection detail"))
    result = call({})
    assert_error(result, "upstream_timeout")
    assert "private" not in json.dumps(result.structured_content)


def test_total_operation_timeout(monkeypatch):
    async def slow(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, json=snapshot())

    transport = httpx.MockTransport(slow)
    monkeypatch.setattr(
        ward_occupancy,
        "_client",
        lambda timeout: httpx.AsyncClient(transport=transport),
    )
    monkeypatch.setattr(
        server_module, "config", replace(server_module.config, student4_api_timeout=0.1)
    )
    assert_error(call({}), "upstream_timeout")


def test_student4_unavailable(mock_api):
    mock_api(error=httpx.ConnectError("private host address"))
    result = call({})
    assert_error(result, "upstream_unavailable")
    assert "private" not in json.dumps(result.structured_content)


@pytest.mark.parametrize(
    "status,code",
    [
        (503, "upstream_unavailable"),
        (500, "upstream_unavailable"),
        (504, "upstream_timeout"),
        (408, "upstream_timeout"),
        (401, "upstream_invalid_response"),
        (404, "upstream_invalid_response"),
        (302, "upstream_invalid_response"),
    ],
)
def test_http_failures(status, code, mock_api):
    mock_api(status=status, text="private upstream error detail")
    result = call({})
    assert_error(result, code)
    assert "private" not in json.dumps(result.structured_content)


def test_redirect_is_not_followed(mock_api):
    requests = mock_api(
        handler=lambda request: httpx.Response(
            302, headers={"Location": "http://other.example/private"}
        )
    )
    assert_error(call({}), "upstream_invalid_response")
    assert len(requests) == 1


def test_non_json(mock_api):
    mock_api(text="not JSON; patient_id=123")
    assert_error(call({}), "upstream_invalid_response")


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"success": False, "error": "private", "data": None},
        {"success": True, "error": None, "data": {}},
    ],
)
def test_invalid_envelope(body, mock_api):
    mock_api(body)
    assert_error(call({}), "upstream_invalid_response")


@pytest.mark.parametrize(
    "field,value",
    [
        ("ward", None),
        ("ward", ""),
        ("total_beds", True),
        ("occupied", -1),
        ("available", "2"),
        ("occupied", 2),
        ("monitored_beds", 4),
        ("occupancy_pct", 66.7),
        ("occupancy_pct", "33.3"),
        ("care_categories", ["Unknown"]),
        ("care_categories", ["Short-term", "Short-term"]),
    ],
)
def test_invalid_aggregates(field, value, mock_api):
    body = single_ward()
    body["data"]["wards"][0][field] = value
    mock_api(body)
    assert_error(call({}), "upstream_invalid_response")


def test_missing_required_field(mock_api):
    body = single_ward()
    del body["data"]["wards"][0]["reserved"]
    mock_api(body)
    assert_error(call({}), "upstream_invalid_response")


def test_missing_envelope_error_field(mock_api):
    body = single_ward()
    del body["error"]
    mock_api(body)
    assert_error(call({}), "upstream_invalid_response")


@pytest.mark.parametrize("pct", [float("nan"), float("inf")])
def test_nonfinite_percentage(mock_api, pct):
    body = single_ward()
    body["data"]["wards"][0]["occupancy_pct"] = pct
    mock_api(text=json.dumps(body))
    assert_error(call({}), "upstream_invalid_response")


def test_totals_must_match_wards(mock_api):
    body = single_ward()
    body["data"]["totals"] = snapshot()["data"]["totals"]
    mock_api(body)
    assert_error(call({}), "upstream_invalid_response")


def test_upstream_must_honour_exact_filter(mock_api):
    mock_api(single_ward())
    assert_error(call({"ward": "Intensive Care"}), "upstream_invalid_response")


def test_duplicate_wards_are_invalid(mock_api):
    body = snapshot()
    body["data"]["wards"][1]["ward"] = "Emergency"
    mock_api(body)
    assert_error(call({}), "upstream_invalid_response")


def test_privacy_projection_excludes_all_unapproved_fields(mock_api):
    body = snapshot()
    forbidden = {
        "patient_id": 123,
        "admission_id": 456,
        "arrangement_id": 789,
        "patient_name": "Private patient",
        "surgeon_name": "Private surgeon",
        "procedure_name": "Private procedure",
        "room_id": 12,
        "bed_id": 13,
        "notes": "Private notes",
        "session": {"patient_id": 123},
    }
    body.update(copy.deepcopy(forbidden))
    body["data"].update(copy.deepcopy(forbidden))
    body["data"]["totals"].update(copy.deepcopy(forbidden))
    for row in body["data"]["wards"]:
        row.update(copy.deepcopy(forbidden))
    mock_api(body)
    result = call({})
    assert result.is_error is False
    serialized = json.dumps(result.structured_content)
    for field in forbidden:
        assert f'"{field}"' not in serialized
    assert "Private" not in serialized
    assert json.loads(result.content[0].text) == result.structured_content


def test_deterministic_structured_output(mock_api):
    mock_api()
    first, second = call({}), call({})
    assert first.structured_content == second.structured_content
    assert first.content == second.content


def test_upstream_config_defaults(monkeypatch):
    monkeypatch.delenv("HOMS_STUDENT4_API_URL", raising=False)
    monkeypatch.delenv("HOMS_STUDENT4_API_TIMEOUT", raising=False)
    config = server_module.load_config()
    assert config.student4_api_url == "http://127.0.0.1:5400/api"
    assert config.student4_api_timeout == 10.0


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ftp://localhost/api",
        "http://user:pass@localhost/api",
        "http://localhost/api?secret=value",
        "http://localhost/api#fragment",
        "http://localhost:invalid/api",
        "http://localhost:0/api",
        "http://@localhost/api",
    ],
)
def test_invalid_upstream_url(value, monkeypatch):
    monkeypatch.setenv("HOMS_STUDENT4_API_URL", value)
    with pytest.raises(ValueError, match="HOMS_STUDENT4_API_URL"):
        server_module.load_config()


@pytest.mark.parametrize("value", ["", "invalid", "nan", "inf", "0", "0.09", "30.1"])
def test_invalid_timeout(value, monkeypatch):
    monkeypatch.setenv("HOMS_STUDENT4_API_TIMEOUT", value)
    with pytest.raises(ValueError, match="HOMS_STUDENT4_API_TIMEOUT"):
        server_module.load_config()


@pytest.mark.parametrize("value", ["0.1", "30"])
def test_timeout_bounds(value, monkeypatch):
    monkeypatch.setenv("HOMS_STUDENT4_API_TIMEOUT", value)
    assert server_module.load_config().student4_api_timeout == float(value)
