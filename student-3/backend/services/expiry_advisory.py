"""Read-only expiry and waste advisory orchestration.

This service contains no database writes. It only gathers HTTP data, asks the
shared AI client for advice, and falls back to an explicit deterministic rule.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import logging
from typing import Any, Callable

from services.ai_client import AIResult, OLLAMA_MODEL, run_prompt


PROMPT_NAME = "expiry_advisory"
PROMPT_VERSION = "v2"
MAX_BATCHES = 6
VALID_ACTIONS = {"use_first", "reduce_next_order", "write_off", "no_action"}
VALID_PRIORITIES = {"high", "medium", "low"}
logger = logging.getLogger("student3.ai")

ITEM_SCHEMA = {
    "batch_id": int,
    "batch_number": str,
    "medicine_name": str,
    "quantity_remaining": int,
    "days_until_expiry": int,
    "projected_waste_units": int,
    "projected_waste_value": (int, float),
    "recommended_action": str,
    "priority": str,
    "reasoning": str,
}
OUTPUT_SCHEMA = {"items": [ITEM_SCHEMA], "summary": str}


def _validate_ai_advisory(data: Any, candidates: list[dict[str, Any]]) -> str | None:
    """Reject model output that is structurally unsafe or omits a batch."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return "response must contain an items array"
    candidates_by_id = {item["batch_id"]: item for item in candidates}
    if {item.get("batch_id") for item in data["items"]} != set(candidates_by_id):
        return "response items must cover exactly the selected batches"
    for item in data["items"]:
        candidate = candidates_by_id[item["batch_id"]]
        if item["recommended_action"] not in VALID_ACTIONS:
            return "recommended_action is invalid"
        if item["priority"] not in VALID_PRIORITIES:
            return "priority is invalid"
        if item["projected_waste_units"] < 0 or item["projected_waste_units"] > candidate["quantity_remaining"]:
            return "projected_waste_units must be between zero and quantity_remaining"
        if item["projected_waste_value"] < 0:
            return "projected_waste_value cannot be negative"
    return None


def _normalise_ai_items(items: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep backend facts authoritative while retaining the model's advisory."""
    candidates_by_id = {item["batch_id"]: item for item in candidates}
    normalised = []
    for item in items:
        candidate = candidates_by_id[item["batch_id"]]
        projected_units = item["projected_waste_units"]
        normalised.append({
            **item,
            "batch_number": candidate["batch_number"],
            "medicine_name": candidate["medicine_name"],
            "quantity_remaining": candidate["quantity_remaining"],
            "days_until_expiry": candidate["days_until_expiry"],
            "projected_waste_value": round(projected_units * candidate["unit_price"], 2),
            "ai_reviewed": True,
        })
    return normalised


def candidates_for(fetch: Callable[[str], list[dict[str, Any]]], days_ahead: int) -> list[dict[str, Any]]:
    """Gather bounded expiry candidates and the last-30-day issue rate."""
    today = date.today()
    deadline = today + timedelta(days=days_ahead)
    batches = fetch("/batches?include_expired=true&include_empty=false")
    medicines = {item["medicine_id"]: item for item in fetch("/medicines")}
    issued_since = today - timedelta(days=30)
    issued_by_medicine: dict[int, int] = {}
    for movement in fetch(f"/stock_movements?from={issued_since.isoformat()}&to={today.isoformat()}"):
        if movement["movement_type"] == "issue":
            issued_by_medicine[movement["medicine_id"]] = issued_by_medicine.get(movement["medicine_id"], 0) + movement["quantity"]

    rows = []
    for batch in batches:
        expiry = date.fromisoformat(batch["expiry_date"])
        if expiry > deadline:
            continue
        medicine = medicines.get(batch["medicine_id"])
        if not medicine:
            continue
        rows.append({
            "batch_id": batch["batch_id"],
            "batch_number": batch["batch_number"],
            "medicine_id": batch["medicine_id"],
            "medicine_name": medicine["name"],
            "quantity_remaining": batch["quantity_remaining"],
            "days_until_expiry": (expiry - today).days,
            "unit_price": medicine["unit_price"],
            "issued_last_30_days": issued_by_medicine.get(batch["medicine_id"], 0),
            "daily_usage_rate": round(issued_by_medicine.get(batch["medicine_id"], 0) / 30, 2),
        })
    return sorted(rows, key=lambda item: (item["days_until_expiry"], item["batch_id"]))


def fallback(candidates: list[dict[str, Any]], *, ai_reviewed: bool = False) -> dict[str, Any]:
    """Calculate the documented deterministic, advisory-only fallback."""
    items = []
    for candidate in candidates:
        remaining = candidate["quantity_remaining"]
        days = candidate["days_until_expiry"]
        projected_units = max(0, round(remaining - (candidate["daily_usage_rate"] * max(days, 0))))
        if days < 0:
            action, priority = "write_off", "high"
        elif days <= 7:
            action, priority = "use_first", "high"
        elif days <= 30 and projected_units:
            action, priority = "reduce_next_order", "medium"
        else:
            action, priority = "no_action", "low"
        items.append({
            **{key: candidate[key] for key in ("batch_id", "batch_number", "medicine_name", "quantity_remaining", "days_until_expiry")},
            "projected_waste_units": projected_units,
            "projected_waste_value": round(projected_units * candidate["unit_price"], 2),
            "recommended_action": action,
            "priority": priority,
            "reasoning": (
                f"Issued {candidate['issued_last_30_days']} units in the last 30 days "
                f"({candidate['daily_usage_rate']:.2f} units/day); this leaves an estimated "
                f"{projected_units} units unused by expiry."
            ),
            "ai_reviewed": ai_reviewed,
        })
    return {"items": items, "summary": "Recommendations use current quantities and the previous 30 days of issued stock."}


def advisory(fetch: Callable[[str], list[dict[str, Any]]], days_ahead: int) -> tuple[dict[str, Any], dict[str, Any], AIResult | None]:
    """Return advisory, exact model input, and optional model result; never writes."""
    rows = candidates_for(fetch, days_ahead)
    selected = rows[:MAX_BATCHES]
    capped = len(rows) > len(selected)
    public_input = [
        {key: item[key] for key in ("batch_id", "batch_number", "medicine_name", "quantity_remaining", "days_until_expiry", "unit_price", "issued_last_30_days", "daily_usage_rate")}
        for item in selected
    ]
    if not selected:
        return ({"items": [], "summary": "No non-empty batches are expired or within the selected expiry window.", "source": "fallback", "fallback_reason": "No eligible batches", "capped": False}, public_input, None)

    result = run_prompt(PROMPT_NAME, OUTPUT_SCHEMA, version=PROMPT_VERSION,
                        values={"days_ahead": days_ahead, "batches_json": json.dumps(public_input, sort_keys=True)})
    validation_error = _validate_ai_advisory(result.data, selected) if result.ok else result.error
    if result.ok and not validation_error:
        payload = result.data
        payload["items"] = _normalise_ai_items(payload["items"], selected)
        remaining = fallback(rows[MAX_BATCHES:])["items"]
        payload["items"].extend(remaining)
        if remaining:
            payload["summary"] += " Remaining eligible batches use the rule-based advisory because they were not AI-reviewed."
        payload.update({"source": "ai", "capped": capped, "model": result.model,
                        "prompt_name": PROMPT_NAME, "prompt_version": PROMPT_VERSION})
    else:
        payload = fallback(rows)
        payload.update({"source": "fallback", "fallback_reason": validation_error or "AI response unavailable",
                        "capped": capped, "model": OLLAMA_MODEL,
                        "prompt_name": PROMPT_NAME, "prompt_version": PROMPT_VERSION})
    priority_order = {"high": 0, "medium": 1, "low": 2}
    payload["items"].sort(key=lambda item: (priority_order[item["priority"]], item["days_until_expiry"], item["batch_id"]))
    logger.info("Expiry advisory | prompt=%s_%s | model=%s | duration=%.3fs | items=%d | outcome=%s | fallback=%s | eval_tokens=%s | tokens_per_second=%s",
                PROMPT_NAME, PROMPT_VERSION, result.model, result.duration_seconds, len(selected),
                result.outcome if result else "no_candidates", payload["source"] == "fallback",
                result.eval_count if result and result.eval_count is not None else "n/a",
                f"{result.tokens_per_second:.2f}" if result and result.tokens_per_second is not None else "n/a")
    return payload, public_input, result
