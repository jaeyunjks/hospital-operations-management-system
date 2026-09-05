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
PROMPT_VERSION = "v3"
MAX_BATCHES = 5
VALID_ACTIONS = {"use_first", "reduce_next_order", "write_off", "no_action"}
VALID_PRIORITIES = {"high", "medium", "low"}
logger = logging.getLogger("student3.ai")

MODEL_ITEM_SCHEMA = {
    "recommended_action": str,
    "priority": str,
    "reasoning": str,
}
OUTPUT_SCHEMA = {"items": [dict]}
FACT_KEYS = (
    "batch_id", "batch_number", "medicine_name", "quantity_remaining",
    "days_until_expiry", "daily_usage_rate", "projected_waste_units",
    "projected_waste_value",
)


def _valid_model_item(item: Any, candidate: dict[str, Any]) -> bool:
    """Accept only the three bounded advisory fields from a model response."""
    if not isinstance(item, dict):
        return False
    action, priority, reasoning = (
        item.get("recommended_action"), item.get("priority"), item.get("reasoning"),
    )
    if action not in VALID_ACTIONS or priority not in VALID_PRIORITIES:
        return False
    if not isinstance(reasoning, str) or not reasoning.strip() or len(reasoning.split()) > 20:
        return False
    usage_rate = f"{candidate['daily_usage_rate']:.2f}"
    return usage_rate in reasoning and "units/day" in reasoning


def _fallback_item(candidate: dict[str, Any]) -> dict[str, Any]:
    """Build one deterministic advisory item from backend-computed facts."""
    if candidate["days_until_expiry"] < 0:
        action, priority = "write_off", "high"
    elif candidate["days_until_expiry"] <= 7:
        action, priority = "use_first", "high"
    elif candidate["days_until_expiry"] <= 30 and candidate["projected_waste_units"]:
        action, priority = "reduce_next_order", "medium"
    else:
        action, priority = "no_action", "low"
    return {
        **{key: candidate[key] for key in FACT_KEYS},
        "recommended_action": action,
        "priority": priority,
        "reasoning": (
            f"Actual usage is {candidate['daily_usage_rate']:.2f} units/day, "
            f"leaving {candidate['projected_waste_units']} units by expiry."
        ),
        "ai_reviewed": False,
    }


def _normalise_ai_item(item: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Attach backend facts to one valid, bounded model recommendation."""
    return {
        **{key: candidate[key] for key in FACT_KEYS},
        "recommended_action": item["recommended_action"],
        "priority": item["priority"],
        "reasoning": item["reasoning"].strip(),
        "ai_reviewed": True,
    }


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
        daily_usage_rate = round(issued_by_medicine.get(batch["medicine_id"], 0) / 30, 2)
        projected_waste_units = max(0, round(
            batch["quantity_remaining"] - (daily_usage_rate * max((expiry - today).days, 0))
        ))
        rows.append({
            "batch_id": batch["batch_id"],
            "batch_number": batch["batch_number"],
            "medicine_id": batch["medicine_id"],
            "medicine_name": medicine["name"],
            "quantity_remaining": batch["quantity_remaining"],
            "days_until_expiry": (expiry - today).days,
            "unit_price": medicine["unit_price"],
            "issued_last_30_days": issued_by_medicine.get(batch["medicine_id"], 0),
            "daily_usage_rate": daily_usage_rate,
            "projected_waste_units": projected_waste_units,
            "projected_waste_value": round(projected_waste_units * medicine["unit_price"], 2),
        })
    return sorted(rows, key=lambda item: (item["days_until_expiry"], item["batch_id"]))


def fallback(candidates: list[dict[str, Any]], *, ai_reviewed: bool = False) -> dict[str, Any]:
    """Calculate the documented deterministic, advisory-only fallback."""
    del ai_reviewed  # Fallback items are always explicitly marked as not AI-reviewed.
    return {"items": [_fallback_item(candidate) for candidate in candidates],
            "summary": "Recommendations use current quantities and the previous 30 days of issued stock."}


def advisory(fetch: Callable[[str], list[dict[str, Any]]], days_ahead: int) -> tuple[dict[str, Any], dict[str, Any], AIResult | None]:
    """Return advisory, exact model input, and optional model result; never writes."""
    rows = candidates_for(fetch, days_ahead)
    selected = rows[:MAX_BATCHES]
    capped = len(rows) > len(selected)
    public_input = [
        {key: item[key] for key in FACT_KEYS}
        for item in selected
    ]
    if not selected:
        return ({"items": [], "summary": "No non-empty batches are expired or within the selected expiry window.", "source": "fallback", "fallback_reason": "No eligible batches", "capped": False}, public_input, None)

    result = run_prompt(PROMPT_NAME, OUTPUT_SCHEMA, version=PROMPT_VERSION,
                        values={"days_ahead": days_ahead, "batches_json": json.dumps(public_input, sort_keys=True)})
    model_items = result.data.get("items") if result.ok and isinstance(result.data, dict) else None
    if not isinstance(model_items, list) or not model_items:
        payload = fallback(rows)
        payload.update({"source": "fallback", "fallback_reason": result.error or "AI returned no usable advisory items",
                        "capped": capped, "model": OLLAMA_MODEL,
                        "prompt_name": PROMPT_NAME, "prompt_version": PROMPT_VERSION})
    else:
        selected_items, ai_item_count = [], 0
        for index, candidate in enumerate(selected):
            item = model_items[index] if index < len(model_items) else None
            if _valid_model_item(item, candidate):
                selected_items.append(_normalise_ai_item(item, candidate))
                ai_item_count += 1
            else:
                # One bad model item must not discard other usable advice.
                selected_items.append(_fallback_item(candidate))
        if ai_item_count == 0:
            payload = fallback(rows)
            payload.update({"source": "fallback", "fallback_reason": "AI returned no usable advisory items",
                            "capped": capped, "model": result.model,
                            "prompt_name": PROMPT_NAME, "prompt_version": PROMPT_VERSION})
        else:
            remaining = fallback(rows[MAX_BATCHES:])["items"]
            payload = {
                "items": selected_items + remaining,
                "summary": (
                    f"{ai_item_count} of {len(selected)} soonest-expiring batches were AI-reviewed; "
                    "all quantities and waste estimates are calculated from current inventory data."
                ),
                "source": "ai",
                "capped": capped,
                "model": result.model,
                "prompt_name": PROMPT_NAME,
                "prompt_version": PROMPT_VERSION,
            }
            if remaining or ai_item_count < len(selected):
                payload["summary"] += " Remaining items use the rule-based advisory and are not AI-reviewed."
    priority_order = {"high": 0, "medium": 1, "low": 2}
    payload["items"].sort(key=lambda item: (priority_order[item["priority"]], item["days_until_expiry"], item["batch_id"]))
    logger.info("Expiry advisory | prompt=%s_%s | model=%s | duration=%.3fs | items=%d | outcome=%s | fallback=%s | eval_tokens=%s | tokens_per_second=%s",
                PROMPT_NAME, PROMPT_VERSION, result.model, result.duration_seconds, len(selected),
                result.outcome if result else "no_candidates", payload["source"] == "fallback",
                result.eval_count if result and result.eval_count is not None else "n/a",
                f"{result.tokens_per_second:.2f}" if result and result.tokens_per_second is not None else "n/a")
    return payload, public_input, result
