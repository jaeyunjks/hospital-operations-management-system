"""Read-only reorder recommendations with bounded AI judgement.

All stock, consumption, lead-time and quantity calculations remain deterministic
and backend-owned. Ollama may only classify priority and explain the supplied
facts; it can never change an order quantity.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
from typing import Any, Callable

from services.ai_client import AIResult, OLLAMA_MODEL, run_prompt


PROMPT_NAME = "reorder_recommendation"
PROMPT_VERSION = "v1"
MAX_MEDICINES = 5
OPEN_STATUSES = {"pending_approval", "approved", "ordered"}
VALID_PRIORITIES = {"high", "medium", "low"}

FACT_KEYS = (
    "medicine_id", "medicine_name", "supplier_id", "supplier_name",
    "available_quantity", "daily_usage_rate", "lead_time_days",
    "open_order_quantity", "near_expiry_quantity", "suggested_quantity",
    "unit_price",
)
OUTPUT_SCHEMA = {"items": [dict]}


def candidates_for(fetch: Callable[[str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Calculate candidate orders from database-service records only."""
    today = date.today()
    medicines = [item for item in fetch("/medicines") if item.get("status") == "active"]
    suppliers = {item["supplier_id"]: item for item in fetch("/suppliers")}
    batches = fetch("/batches?include_expired=false&include_empty=false")
    orders = fetch("/purchase_orders")
    since = today - timedelta(days=30)
    issues: dict[int, int] = {}
    for movement in fetch(f"/stock_movements?from={since.isoformat()}&to={today.isoformat()}"):
        if movement.get("movement_type") == "issue":
            medicine_id = movement["medicine_id"]
            issues[medicine_id] = issues.get(medicine_id, 0) + movement["quantity"]

    batches_by_medicine: dict[int, list[dict[str, Any]]] = {}
    for batch in batches:
        batches_by_medicine.setdefault(batch["medicine_id"], []).append(batch)
    open_by_medicine: dict[int, int] = {}
    for order in orders:
        if order.get("status") in OPEN_STATUSES:
            outstanding = max(0, order.get("quantity_ordered", 0) - order.get("quantity_received", 0))
            open_by_medicine[order["medicine_id"]] = open_by_medicine.get(order["medicine_id"], 0) + outstanding

    rows = []
    for medicine in medicines:
        supplier = suppliers.get(medicine.get("supplier_id"))
        if not supplier or supplier.get("status") != "active":
            continue
        medicine_batches = batches_by_medicine.get(medicine["medicine_id"], [])
        available = sum(batch["quantity_remaining"] for batch in medicine_batches)
        if available > medicine["reorder_level"]:
            continue
        lead_time = supplier["lead_time_days"]
        near_expiry = sum(
            batch["quantity_remaining"] for batch in medicine_batches
            if date.fromisoformat(batch["expiry_date"]) <= today + timedelta(days=lead_time)
        )
        daily_rate = round(issues.get(medicine["medicine_id"], 0) / 30, 2)
        outstanding = open_by_medicine.get(medicine["medicine_id"], 0)
        usable_available = max(0, available - near_expiry)
        coverage_target = max(medicine["reorder_level"], math.ceil(daily_rate * (lead_time + 7)))
        suggested = max(0, coverage_target - usable_available - outstanding)
        if suggested <= 0:
            continue
        rows.append({
            "medicine_id": medicine["medicine_id"],
            "medicine_name": medicine["name"],
            "supplier_id": supplier["supplier_id"],
            "supplier_name": supplier["name"],
            "available_quantity": available,
            "daily_usage_rate": daily_rate,
            "lead_time_days": lead_time,
            "open_order_quantity": outstanding,
            "near_expiry_quantity": near_expiry,
            "suggested_quantity": suggested,
            "unit_price": medicine["unit_price"],
        })
    return sorted(rows, key=lambda item: (-item["daily_usage_rate"], item["available_quantity"], item["medicine_id"]))


def _fallback_item(candidate: dict[str, Any]) -> dict[str, Any]:
    priority = "high" if candidate["available_quantity"] == 0 else "medium" if candidate["daily_usage_rate"] else "low"
    return {
        **{key: candidate[key] for key in FACT_KEYS},
        "priority": priority,
        "reasoning": f"Actual usage is {candidate['daily_usage_rate']:.2f} units/day, so maintain supply through the lead time.",
        "adjustment_flag": False,
        "adjustment_reason": None,
        "ai_reviewed": False,
    }


def _valid_model_item(item: Any, candidate: dict[str, Any]) -> bool:
    if not isinstance(item, dict) or item.get("priority") not in VALID_PRIORITIES:
        return False
    reasoning = item.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip() or len(reasoning.split()) > 20:
        return False
    if f"{candidate['daily_usage_rate']:.2f}" not in reasoning or "units/day" not in reasoning:
        return False
    adjustment = item.get("adjustment_flag", False)
    if not isinstance(adjustment, bool):
        return False
    if adjustment and (not isinstance(item.get("adjustment_reason"), str) or not item["adjustment_reason"].strip()):
        return False
    return True


def _ai_item(item: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: candidate[key] for key in FACT_KEYS},
        "priority": item["priority"],
        "reasoning": item["reasoning"].strip(),
        "adjustment_flag": item.get("adjustment_flag", False),
        "adjustment_reason": str(item.get("adjustment_reason") or "").strip() or None,
        "ai_reviewed": True,
    }


def advisory(fetch: Callable[[str], list[dict[str, Any]]]) -> tuple[dict[str, Any], list[dict[str, Any]], AIResult | None]:
    """Return AI-assisted recommendations with deterministic safety fallbacks."""
    rows = candidates_for(fetch)
    selected = rows[:MAX_MEDICINES]
    capped = len(rows) > len(selected)
    public_input = [{key: item[key] for key in FACT_KEYS} for item in selected]
    if not selected:
        return ({"items": [], "summary": "No medicines currently require a suggested order.",
                 "source": "fallback", "fallback_reason": "No eligible medicines", "capped": False}, public_input, None)
    result = run_prompt(PROMPT_NAME, OUTPUT_SCHEMA, version=PROMPT_VERSION,
                        values={"medicines_json": json.dumps(public_input, sort_keys=True)})
    model_items = result.data.get("items") if result.ok and isinstance(result.data, dict) else None
    if not isinstance(model_items, list) or not model_items:
        payload = {"items": [_fallback_item(item) for item in rows],
                   "summary": "Rule-based recommendations use current stock, usage, lead time and open orders.",
                   "source": "fallback", "fallback_reason": result.error or "AI returned no usable recommendations"}
    else:
        reviewed, valid_count = [], 0
        for index, candidate in enumerate(selected):
            item = model_items[index] if index < len(model_items) else None
            if _valid_model_item(item, candidate):
                reviewed.append(_ai_item(item, candidate))
                valid_count += 1
            else:
                reviewed.append(_fallback_item(candidate))
        if valid_count == 0:
            payload = {"items": [_fallback_item(item) for item in rows],
                       "summary": "Rule-based recommendations use current stock, usage, lead time and open orders.",
                       "source": "fallback", "fallback_reason": "AI returned no usable recommendations"}
        else:
            payload = {"items": reviewed + [_fallback_item(item) for item in rows[MAX_MEDICINES:]],
                       "summary": f"{valid_count} of {len(selected)} recommendations were AI-reviewed; quantities remain backend-calculated.",
                       "source": "ai"}
    payload.update({"capped": capped, "model": result.model if result else OLLAMA_MODEL,
                    "prompt_name": PROMPT_NAME, "prompt_version": PROMPT_VERSION})
    return payload, public_input, result
