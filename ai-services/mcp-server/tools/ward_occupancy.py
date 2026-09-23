"""Read-only access to Student 4's published aggregate occupancy contract."""

from __future__ import annotations

import asyncio
import math
from typing import Any, Dict

import httpx

TOOL_NAME = "homs_ward_occupancy_status"
MAX_WARD_LENGTH = 200
SOURCE = "student-4-room-bed-api"
COUNT_FIELDS = ("total_beds", "occupied", "available", "reserved", "maintenance")
CARE_CATEGORIES = {"Surgical", "Short-term", "Long-term"}

_COUNTS_SCHEMA = {field: {"type": "integer", "minimum": 0} for field in COUNT_FIELDS}
_PERCENT_SCHEMA = {"type": "number", "minimum": 0, "maximum": 100}
_WARD_PROPERTIES = {
    "ward": {"type": "string", "minLength": 1, "maxLength": MAX_WARD_LENGTH},
    **_COUNTS_SCHEMA,
    "monitored_beds": {"type": "integer", "minimum": 0},
    "occupancy_pct": _PERCENT_SCHEMA,
    "care_categories": {
        "type": "array",
        "items": {"enum": sorted(CARE_CATEGORIES)},
        "uniqueItems": True,
    },
}
_TOTAL_PROPERTIES = {**_COUNTS_SCHEMA, "occupancy_pct": _PERCENT_SCHEMA}
DATA_SCHEMA = {
    "type": "object",
    "properties": {
        "requested_ward": {"type": ["string", "null"]},
        "wards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _WARD_PROPERTIES,
                "required": list(_WARD_PROPERTIES),
                "additionalProperties": False,
            },
        },
        "totals": {
            "type": "object",
            "properties": _TOTAL_PROPERTIES,
            "required": list(_TOTAL_PROPERTIES),
            "additionalProperties": False,
        },
        "source": {"const": SOURCE},
    },
    "required": ["requested_ward", "wards", "totals", "source"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"const": "1.0"},
        "ok": {"type": "boolean"},
        "tool": {"const": TOOL_NAME},
        "data": {"anyOf": [DATA_SCHEMA, {"type": "null"}]},
        "error": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "details": {"type": "object"},
                    },
                    "required": ["code", "message", "details"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["schema_version", "ok", "tool", "data", "error"],
    "additionalProperties": False,
}


def tool_error(
    code: str, message: str, details: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """Use the existing schema-versioned MCP error envelope, without upstream text."""

    return {
        "schema_version": "1.0",
        "ok": False,
        "tool": TOOL_NAME,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def _client(timeout: float) -> httpx.AsyncClient:
    # Neither caller-controlled destinations nor redirects/proxies may widen access.
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)


def _counts(row: object) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("Expected aggregate object")
    counts = {}
    for field in COUNT_FIELDS:
        value = row.get(field)
        if type(value) is not int or value < 0:
            raise ValueError("Invalid count")
        counts[field] = value
    if sum(counts[field] for field in COUNT_FIELDS[1:]) != counts["total_beds"]:
        raise ValueError("Inconsistent bed counts")
    pct = row.get("occupancy_pct")
    if type(pct) not in (int, float) or not math.isfinite(pct) or not 0 <= pct <= 100:
        raise ValueError("Invalid percentage")
    expected = (
        round(counts["occupied"] * 100.0 / counts["total_beds"], 1)
        if counts["total_beds"]
        else 0.0
    )
    if pct != expected:
        raise ValueError("Inconsistent percentage")
    return {**counts, "occupancy_pct": pct}


def _project(body: object, requested_ward: str | None) -> Dict[str, Any]:
    """Validate authoritative aggregates, then construct a strict field allowlist."""

    if (
        not isinstance(body, dict)
        or body.get("success") is not True
        or "error" not in body
        or body["error"] is not None
    ):
        raise ValueError("Invalid Student 4 envelope")
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("wards"), list):
        raise ValueError("Missing ward aggregates")
    wards = []
    seen = set()
    for row in data["wards"]:
        counts = _counts(row)
        ward = row.get("ward")
        if (
            not isinstance(ward, str)
            or not ward.strip()
            or len(ward) > MAX_WARD_LENGTH
            or ward in seen
        ):
            raise ValueError("Invalid or duplicate ward")
        if requested_ward is not None and ward != requested_ward:
            raise ValueError("Upstream did not honour exact ward filter")
        seen.add(ward)
        monitored = row.get("monitored_beds")
        if type(monitored) is not int or not 0 <= monitored <= counts["total_beds"]:
            raise ValueError("Invalid monitored bed count")
        categories = row.get("care_categories")
        if not isinstance(categories, list) or any(
            not isinstance(c, str) or c not in CARE_CATEGORIES for c in categories
        ):
            raise ValueError("Invalid care categories")
        if len(set(categories)) != len(categories):
            raise ValueError("Duplicate care categories")
        wards.append(
            {
                "ward": ward,
                **counts,
                "monitored_beds": monitored,
                "care_categories": list(categories),
            }
        )
    totals = _counts(data.get("totals"))
    if any(totals[field] != sum(row[field] for row in wards) for field in COUNT_FIELDS):
        raise ValueError("Totals disagree with ward aggregates")
    return {
        "requested_ward": requested_ward,
        "wards": wards,
        "totals": totals,
        "source": SOURCE,
    }


async def homs_ward_occupancy_status(
    ward: object = None, *, api_url: str, timeout: float
) -> Dict[str, Any]:
    """Fetch the current snapshot; never map departments or infer staffing rules."""

    if ward is not None:
        if not isinstance(ward, str):
            return tool_error(
                "validation_error",
                "'ward' must be a string or null.",
                {
                    "field": "ward",
                    "reason": "wrong_type",
                    "received_type": type(ward).__name__,
                },
            )
        if not ward.strip():
            return tool_error(
                "validation_error",
                "'ward' must not be blank.",
                {"field": "ward", "reason": "blank"},
            )
        if len(ward) > MAX_WARD_LENGTH:
            return tool_error(
                "validation_error",
                f"'ward' must be at most {MAX_WARD_LENGTH} characters.",
                {
                    "field": "ward",
                    "reason": "too_long",
                    "max_length": MAX_WARD_LENGTH,
                    "received_length": len(ward),
                },
            )

    try:
        # Bound the whole operation as well as httpx's individual I/O stages.
        async with asyncio.timeout(timeout):
            async with _client(timeout) as client:
                response = await client.get(
                    api_url.rstrip("/") + "/wards/occupancy",
                    params={"ward": ward} if ward is not None else None,
                    headers={"Accept": "application/json"},
                )
        if response.status_code in (408, 504):
            return tool_error("upstream_timeout", "Student 4 occupancy API timed out.")
        if response.status_code >= 500:
            return tool_error(
                "upstream_unavailable", "Student 4 occupancy API is unavailable."
            )
        if response.status_code != 200:
            return tool_error(
                "upstream_invalid_response",
                "Student 4 occupancy API returned an invalid response.",
            )
        data = _project(response.json(), ward)
    except (TimeoutError, httpx.TimeoutException):
        return tool_error("upstream_timeout", "Student 4 occupancy API timed out.")
    except httpx.RequestError:
        return tool_error(
            "upstream_unavailable", "Student 4 occupancy API is unavailable."
        )
    except (ValueError, TypeError, OverflowError):
        return tool_error(
            "upstream_invalid_response",
            "Student 4 occupancy API returned an invalid response.",
        )

    if ward is not None and not data["wards"]:
        return tool_error(
            "WARD_NOT_FOUND",
            "No exact Student 4 ward match was found.",
            {"field": "ward"},
        )
    return {
        "schema_version": "1.0",
        "ok": True,
        "tool": TOOL_NAME,
        "data": data,
        "error": None,
    }
