"""Read-only access to Student 3's published purchase-order alerts contract."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Dict, List

import httpx

TOOL_NAME = "homs_pharmacy_order_alerts"
SOURCE = "student-3-pharmacy-api"
ALERT_TYPES = ("all", "pending_approval", "overdue")
LIST_LIMIT = 10
MAX_NAME_LENGTH = 200
COUNT_FIELDS = ("pending_approval", "ai_suggested_pending", "approved", "ordered", "overdue")
LIST_STATUSES = {
    "pending_approval": {"pending_approval"},
    "approved": {"approved"},
    "ordered": {"ordered"},
    "overdue": {"approved", "ordered"},
}
# Which lists each alert type returns; the others are null.
LISTS_FOR = {
    "all": ("pending_approval", "approved", "ordered", "overdue"),
    "pending_approval": ("pending_approval",),
    "overdue": ("overdue",),
}

_COUNT = {"type": "integer", "minimum": 0}
_NAME = {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH}
_ORDER_PROPERTIES = {
    "po_id": {"type": "integer", "minimum": 1},
    "medicine_name": _NAME,
    "supplier_name": {"type": ["string", "null"]},
    "status": {"enum": ["pending_approval", "approved", "ordered"]},
    "quantity_ordered": _COUNT,
    "outstanding": _COUNT,
    "total_value": {"type": "number", "minimum": 0},
    "ai_generated": {"type": "boolean"},
    "created_at": {"type": "string"},
    "expected_at": {"type": ["string", "null"]},
    "days_overdue": _COUNT,
}
_ORDER_LIST = {
    "anyOf": [
        {"type": "array", "maxItems": LIST_LIMIT, "items": {
            "type": "object", "properties": _ORDER_PROPERTIES,
            "required": list(_ORDER_PROPERTIES), "additionalProperties": False}},
        {"type": "null"},
    ]
}
DATA_SCHEMA = {
    "type": "object",
    "properties": {
        "alert_type": {"enum": list(ALERT_TYPES)},
        "counts": {"type": "object", "properties": {field: _COUNT for field in COUNT_FIELDS},
                   "required": list(COUNT_FIELDS), "additionalProperties": False},
        **{name: _ORDER_LIST for name in LIST_STATUSES},
        "source": {"const": SOURCE},
    },
    "required": ["alert_type", "counts", *LIST_STATUSES, "source"],
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


def tool_error(code: str, message: str, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
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


def _date_text(value: object, nullable: bool) -> Any:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid date")
    date.fromisoformat(value[:10])
    return value


def _order(row: object, list_name: str) -> Dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("Expected order object")
    po_id = row.get("po_id")
    if type(po_id) is not int or po_id < 1:
        raise ValueError("Invalid order id")
    name = row.get("medicine_name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LENGTH:
        raise ValueError("Invalid medicine name")
    supplier = row.get("supplier_name")
    if supplier is not None and not isinstance(supplier, str):
        raise ValueError("Invalid supplier name")
    status = row.get("status")
    if status not in LIST_STATUSES[list_name]:
        raise ValueError("Order is in the wrong list")
    value = row.get("total_value")
    if type(value) not in (int, float) or value < 0:
        raise ValueError("Invalid order value")
    if type(row.get("ai_generated")) is not bool:
        raise ValueError("Invalid AI flag")
    days_overdue = _count(row.get("days_overdue"))
    if list_name == "overdue" and days_overdue < 1:
        raise ValueError("Overdue order is not overdue")
    return {
        "po_id": po_id,
        "medicine_name": name,
        "supplier_name": supplier,
        "status": status,
        "quantity_ordered": _count(row.get("quantity_ordered")),
        "outstanding": _count(row.get("outstanding")),
        "total_value": value,
        "ai_generated": row["ai_generated"],
        "created_at": _date_text(row.get("created_at"), nullable=False),
        "expected_at": _date_text(row.get("expected_at"), nullable=True),
        "days_overdue": days_overdue,
    }


def _project(body: object, alert_type: str) -> Dict[str, Any]:
    """Validate the order-alert summary, then build a strict allowlist."""

    if not isinstance(body, dict) or not isinstance(body.get("counts"), dict):
        raise ValueError("Invalid Student 3 order alerts body")
    counts = {field: _count(body["counts"].get(field)) for field in COUNT_FIELDS}
    lists: Dict[str, List[Dict[str, Any]]] = {}
    for name in LIST_STATUSES:
        rows = body.get(name)
        if not isinstance(rows, list) or len(rows) > LIST_LIMIT:
            raise ValueError(f"Invalid {name} list")
        lists[name] = [_order(row, name) for row in rows]
        if len(lists[name]) > counts[name]:
            raise ValueError("Order list longer than its count")
        if len(lists[name]) < min(counts[name], LIST_LIMIT):
            raise ValueError("Order list shorter than its count")
    if counts["ai_suggested_pending"] > counts["pending_approval"]:
        raise ValueError("More AI-suggested orders than pending orders")
    if counts["overdue"] > counts["approved"] + counts["ordered"]:
        raise ValueError("More overdue deliveries than open orders")
    return {
        "alert_type": alert_type,
        "counts": counts,
        **{name: (rows if name in LISTS_FOR[alert_type] else None) for name, rows in lists.items()},
        "source": SOURCE,
    }


async def homs_pharmacy_order_alerts(
    alert_type: object = None, *, api_url: str, timeout: float
) -> Dict[str, Any]:
    """Fetch current purchase-order alerts; never approves, orders or cancels."""

    if alert_type is None:
        alert_type = "all"
    if not isinstance(alert_type, str):
        return tool_error(
            "validation_error",
            "'alert_type' must be a string or null.",
            {"field": "alert_type", "reason": "wrong_type", "received_type": type(alert_type).__name__},
        )
    if alert_type not in ALERT_TYPES:
        return tool_error(
            "validation_error",
            f"'alert_type' must be one of: {', '.join(ALERT_TYPES)}.",
            {"field": "alert_type", "reason": "not_allowed", "allowed": list(ALERT_TYPES)},
        )

    try:
        async with asyncio.timeout(timeout):
            async with _client(timeout) as client:
                response = await client.get(
                    api_url.rstrip("/") + "/purchase-orders/alerts",
                    headers={"Accept": "application/json"},
                )
        if response.status_code in (408, 504):
            return tool_error("upstream_timeout", "Student 3 pharmacy API timed out.")
        if response.status_code >= 500:
            return tool_error("upstream_unavailable", "Student 3 pharmacy API is unavailable.")
        if response.status_code != 200:
            return tool_error("upstream_invalid_response", "Student 3 pharmacy API returned an invalid response.")
        data = _project(response.json(), alert_type)
    except (TimeoutError, httpx.TimeoutException):
        return tool_error("upstream_timeout", "Student 3 pharmacy API timed out.")
    except httpx.RequestError:
        return tool_error("upstream_unavailable", "Student 3 pharmacy API is unavailable.")
    except (ValueError, TypeError, OverflowError):
        return tool_error("upstream_invalid_response", "Student 3 pharmacy API returned an invalid response.")

    return {"schema_version": "1.0", "ok": True, "tool": TOOL_NAME, "data": data, "error": None}
