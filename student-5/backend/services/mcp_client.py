"""Narrow adapter from Student 5 to the shared HOMS MCP server.

Only the read-only ``homs_ward_occupancy_status`` tool is exposed. Routes
cannot choose another server or tool, and no direct Student 4 fallback exists.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from mcp import Client

from config import Config

TOOL_NAME = "homs_ward_occupancy_status"
SOURCE = "student-4-room-bed-api"
SCHEMA_VERSION = "1.0"
MAX_WARD_LENGTH = 200
COUNT_FIELDS = ("total_beds", "occupied", "available", "reserved", "maintenance")
WARD_FIELDS = {
    "ward", *COUNT_FIELDS, "monitored_beds", "occupancy_pct", "care_categories"
}
TOTAL_FIELDS = {*COUNT_FIELDS, "occupancy_pct"}
DATA_FIELDS = {"requested_ward", "wards", "totals", "source"}
ENVELOPE_FIELDS = {"schema_version", "ok", "tool", "data", "error"}


class MCPClientError(Exception):
    """Base class for safe, classified adapter failures."""


class MCPDisabledError(MCPClientError):
    pass


class MCPUnavailable(MCPClientError):
    pass


class MCPTimeout(MCPClientError):
    pass


class MCPInvalidResponse(MCPClientError):
    pass


class MCPToolFailure(MCPClientError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_percentage(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 100
    )


def _validate_counts(record: Dict[str, Any], expected_fields: set[str]) -> None:
    if set(record) != expected_fields:
        raise MCPInvalidResponse()
    if not all(_is_count(record.get(field)) for field in COUNT_FIELDS):
        raise MCPInvalidResponse()
    if not _is_percentage(record.get("occupancy_pct")):
        raise MCPInvalidResponse()


def _validated_success(envelope: Any, requested_ward: Optional[str]) -> Dict[str, Any]:
    if not isinstance(envelope, dict) or set(envelope) != ENVELOPE_FIELDS:
        raise MCPInvalidResponse()
    if (
        envelope.get("schema_version") != SCHEMA_VERSION
        or envelope.get("tool") != TOOL_NAME
        or envelope.get("ok") is not True
        or envelope.get("error") is not None
    ):
        raise MCPInvalidResponse()

    data = envelope.get("data")
    if not isinstance(data, dict) or set(data) != DATA_FIELDS:
        raise MCPInvalidResponse()
    if data.get("source") != SOURCE or data.get("requested_ward") != requested_ward:
        raise MCPInvalidResponse()

    wards = data.get("wards")
    totals = data.get("totals")
    if not isinstance(wards, list) or not isinstance(totals, dict):
        raise MCPInvalidResponse()
    _validate_counts(totals, TOTAL_FIELDS)

    for ward in wards:
        if not isinstance(ward, dict):
            raise MCPInvalidResponse()
        _validate_counts(ward, WARD_FIELDS)
        name = ward.get("ward")
        categories = ward.get("care_categories")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > MAX_WARD_LENGTH
            or not _is_count(ward.get("monitored_beds"))
            or not isinstance(categories, list)
            or len(categories) != len(set(categories))
            or any(category not in {"Surgical", "Short-term", "Long-term"}
                   for category in categories)
        ):
            raise MCPInvalidResponse()

    for field in COUNT_FIELDS:
        if totals[field] != sum(ward[field] for ward in wards):
            raise MCPInvalidResponse()
    if requested_ward is not None and (
        len(wards) != 1 or wards[0]["ward"] != requested_ward
    ):
        raise MCPInvalidResponse()
    return envelope


def _tool_error_code(envelope: Any) -> str:
    if not isinstance(envelope, dict):
        raise MCPInvalidResponse()
    error = envelope.get("error")
    if (
        envelope.get("schema_version") != SCHEMA_VERSION
        or envelope.get("tool") != TOOL_NAME
        or envelope.get("ok") is not False
        or envelope.get("data") is not None
        or not isinstance(error, dict)
        or not isinstance(error.get("code"), str)
    ):
        raise MCPInvalidResponse()
    return error["code"]


class WardOccupancyMCPClient:
    """Request-scoped SDK adapter exposing one bounded operation."""

    def __init__(self, enabled: bool = Config.MCP_ENABLED,
                 server_url: str = Config.MCP_SERVER_URL,
                 timeout: float = Config.MCP_TIMEOUT):
        self.enabled = enabled
        self.server_url = server_url
        self.timeout = timeout

    def get_ward_occupancy(self, ward: Optional[str] = None) -> Dict[str, Any]:
        if not self.enabled:
            raise MCPDisabledError()
        return asyncio.run(self._get_ward_occupancy(ward))

    async def _get_ward_occupancy(self, ward: Optional[str]) -> Dict[str, Any]:
        parsed = urlsplit(self.server_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise MCPInvalidResponse()

        arguments = {} if ward is None else {"ward": ward}
        try:
            async with asyncio.timeout(self.timeout):
                async with Client(
                    self.server_url, read_timeout_seconds=self.timeout
                ) as client:
                    result = await client.call_tool(
                        TOOL_NAME, arguments, read_timeout_seconds=self.timeout
                    )
        except (TimeoutError, asyncio.TimeoutError) as error:
            raise MCPTimeout() from error
        except MCPClientError:
            raise
        except Exception as error:
            raise MCPUnavailable() from error

        envelope = result.structured_content
        if result.is_error:
            raise MCPToolFailure(_tool_error_code(envelope))
        return _validated_success(envelope, ward)

