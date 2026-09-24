"""Read-only access to Student 3's published pharmacy dashboard contract."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List

import httpx

TOOL_NAME = "homs_pharmacy_stock_alerts"
SOURCE = "student-3-pharmacy-api"
ALERT_TYPES = ("all", "low_stock", "expiring_soon")
EXPIRY_WINDOW_DAYS = 30
MAX_NAME_LENGTH = 200
COUNT_FIELDS = (
    "active_medicines",
    "low_stock",
    "expiring_within_30_days",
    "expiring_within_7_days",
    "expired_batches",
    "pending_approvals",
)

_NAME_SCHEMA = {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH}
_COUNT_SCHEMA = {"type": "integer", "minimum": 0}
_LOW_STOCK_PROPERTIES = {
    "name": _NAME_SCHEMA,
    "stock_quantity": _COUNT_SCHEMA,
    "reorder_level": _COUNT_SCHEMA,
    "supplier_name": {"type": ["string", "null"]},
}
_EXPIRING_PROPERTIES = {
    "medicine_name": _NAME_SCHEMA,
    "batch_number": _NAME_SCHEMA,
    "expiry_date": {"type": "string", "format": "date"},
    "quantity_remaining": _COUNT_SCHEMA,
    "days_until_expiry": {
        "type": "integer",
        "minimum": 0,
        "maximum": EXPIRY_WINDOW_DAYS,
    },
}


def _list_schema(properties: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "anyOf": [
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            },
            {"type": "null"},
        ]
    }


DATA_SCHEMA = {
    "type": "object",
    "properties": {
        "alert_type": {"enum": list(ALERT_TYPES)},
        "counts": {
            "type": "object",
            "properties": {field: _COUNT_SCHEMA for field in COUNT_FIELDS},
            "required": list(COUNT_FIELDS),
            "additionalProperties": False,
        },
        "low_stock": _list_schema(_LOW_STOCK_PROPERTIES),
        "expiring_soon": _list_schema(_EXPIRING_PROPERTIES),
        "source": {"const": SOURCE},
    },
    "required": ["alert_type", "counts", "low_stock", "expiring_soon", "source"],
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
    """Use the shared schema-versioned MCP error envelope, without upstream text."""

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


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Invalid count")
    return value


def _name(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME_LENGTH:
        raise ValueError("Invalid name")
    return value


def _low_stock_row(row: object) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("Expected low-stock object")
    stock, reorder = _count(row.get("stock_quantity")), _count(row.get("reorder_level"))
    if stock > reorder:
        raise ValueError("Low-stock row is above its reorder level")
    supplier = row.get("supplier_name")
    if supplier is not None and not isinstance(supplier, str):
        raise ValueError("Invalid supplier name")
    return {
        "name": _name(row.get("name")),
        "stock_quantity": stock,
        "reorder_level": reorder,
        "supplier_name": supplier,
    }


def _expiring_row(row: object) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("Expected expiring-batch object")
    expiry = row.get("expiry_date")
    if not isinstance(expiry, str):
        raise ValueError("Invalid expiry date")
    date.fromisoformat(expiry)
    days = row.get("days_until_expiry")
    if type(days) is not int or not 0 <= days <= EXPIRY_WINDOW_DAYS:
        raise ValueError("Batch is outside the expiry window")
    return {
        "medicine_name": _name(row.get("medicine_name")),
        "batch_number": _name(row.get("batch_number")),
        "expiry_date": expiry,
        "quantity_remaining": _count(row.get("quantity_remaining")),
        "days_until_expiry": days,
    }


def _rows(body: Dict[str, Any], key: str, parse) -> List[Dict[str, Any]]:
    rows = body.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"Missing {key} list")
    return [parse(row) for row in rows]


def _project(body: object, alert_type: str) -> Dict[str, Any]:
    """Validate authoritative dashboard aggregates, then build a strict allowlist.

    Stock movements (which name staff) and scheduled-agent state are discarded.
    """

    if not isinstance(body, dict) or not isinstance(body.get("counts"), dict):
        raise ValueError("Invalid Student 3 dashboard body")
    counts = {field: _count(body["counts"].get(field)) for field in COUNT_FIELDS}
    low_stock = _rows(body, "low_stock", _low_stock_row)
    expiring = _rows(body, "expiring_soon", _expiring_row)
    if (
        counts["low_stock"] > counts["active_medicines"]
        or len(low_stock) > counts["low_stock"]
        or counts["expiring_within_7_days"] > counts["expiring_within_30_days"]
        or len(expiring) > counts["expiring_within_30_days"]
        or sum(row["days_until_expiry"] <= 7 for row in expiring)
        > counts["expiring_within_7_days"]
    ):
        raise ValueError("Dashboard lists disagree with counts")
    return {
        "alert_type": alert_type,
        "counts": counts,
        "low_stock": low_stock if alert_type in ("all", "low_stock") else None,
        "expiring_soon": expiring if alert_type in ("all", "expiring_soon") else None,
        "source": SOURCE,
    }


async def homs_pharmacy_stock_alerts(
    alert_type: object = None, *, api_url: str, timeout: float
) -> Dict[str, Any]:
    """Fetch the current stock alerts; never issues, orders or writes off stock."""

    if alert_type is None:
        alert_type = "all"
    if not isinstance(alert_type, str):
        return tool_error(
            "validation_error",
            "'alert_type' must be a string or null.",
            {
                "field": "alert_type",
                "reason": "wrong_type",
                "received_type": type(alert_type).__name__,
            },
        )
    if alert_type not in ALERT_TYPES:
        return tool_error(
            "validation_error",
            f"'alert_type' must be one of: {', '.join(ALERT_TYPES)}.",
            {"field": "alert_type", "reason": "not_allowed", "allowed": list(ALERT_TYPES)},
        )

    try:
        # Bound the whole operation as well as httpx's individual I/O stages.
        async with asyncio.timeout(timeout):
            async with _client(timeout) as client:
                response = await client.get(
                    api_url.rstrip("/") + "/dashboard/summary",
                    headers={"Accept": "application/json"},
                )
        if response.status_code in (408, 504):
            return tool_error("upstream_timeout", "Student 3 pharmacy API timed out.")
        if response.status_code >= 500:
            return tool_error(
                "upstream_unavailable", "Student 3 pharmacy API is unavailable."
            )
        if response.status_code != 200:
            return tool_error(
                "upstream_invalid_response",
                "Student 3 pharmacy API returned an invalid response.",
            )
        data = _project(response.json(), alert_type)
    except (TimeoutError, httpx.TimeoutException):
        return tool_error("upstream_timeout", "Student 3 pharmacy API timed out.")
    except httpx.RequestError:
        return tool_error(
            "upstream_unavailable", "Student 3 pharmacy API is unavailable."
        )
    except (ValueError, TypeError, OverflowError):
        return tool_error(
            "upstream_invalid_response",
            "Student 3 pharmacy API returned an invalid response.",
        )

    return {
        "schema_version": "1.0",
        "ok": True,
        "tool": TOOL_NAME,
        "data": data,
        "error": None,
    }
