"""Evidence for the pharmacy reorder-decision Multi-Agent workflow.

The backend owns every number: quantities, values and the coverage target come
from ``reorder_recommendation.candidates_for`` (database-service records only).
The Multi-Agent Server receives them as evidence in its generic contract
(objective, options, constraints, unknowns) and never changes inventory.
"""
from __future__ import annotations

import math
from typing import Any

AGENT_BUDGET_CAP = 500.00


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _option(option_id: str, label: str, quantity: int, risks: list[str]) -> dict[str, Any]:
    return {
        "id": option_id,
        "label": label,
        "attributes": {"supplier_active": True, "quantity_positive": quantity > 0},
        "outcomes": {"metric_delta": quantity},
        "risk_indicators": risks,
    }


def build_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    """Turn one reorder candidate into Multi-Agent evidence."""
    available = candidate["available_quantity"]
    near_expiry = candidate["near_expiry_quantity"]
    open_units = candidate["open_order_quantity"]
    suggested = candidate["suggested_quantity"]
    price = candidate["unit_price"]
    usable = max(0, available - near_expiry)
    current = usable + open_units
    target = current + suggested  # coverage target, as calculated by the backend

    shared_risks = []
    if near_expiry:
        shared_risks.append(f"{near_expiry} units expire within the supplier lead time and are not counted as usable.")
    if open_units:
        shared_risks.append(f"{open_units} units are already on open purchase orders.")

    full_value = round(suggested * price, 2)
    full_risks = list(shared_risks)
    if full_value > AGENT_BUDGET_CAP:
        full_risks.append(f"Order value {_money(full_value)} is above the {_money(AGENT_BUDGET_CAP)} "
                          "per-cycle cap used by the scheduled agent.")
    options = [_option("order_suggested", f"Order the suggested {suggested} units ({_money(full_value)})",
                       suggested, full_risks)]
    half = math.ceil(suggested / 2)
    if half < suggested:
        options.append(_option(
            "order_half", f"Order half, {half} units ({_money(round(half * price, 2))})", half,
            shared_risks + [f"Projected stock of {current + half} units stays below the coverage target of {target}."]))
    options.append(_option("defer", "Do not order now", 0,
                           [f"Stock stays at {current} units, below the coverage target of {target}."]))

    unknowns = ["Demand beyond the last 30 days of issues is not known."]
    if not candidate["daily_usage_rate"]:
        unknowns.append("No stock was issued in the last 30 days, so the usage rate may underestimate demand.")

    return {
        "scenario": "Pharmacy reorder decision",
        "current_state": {
            "medicine": candidate["medicine_name"],
            "supplier": candidate["supplier_name"],
            "available_units": available,
            "near_expiry_units": near_expiry,
            "open_order_units": open_units,
            "daily_usage_rate": candidate["daily_usage_rate"],
            "lead_time_days": candidate["lead_time_days"],
            "unit_price": price,
        },
        "objective": {"metric": "usable_stock_units", "current": current, "target": target, "unit": "units"},
        "options": options,
        "constraints": [
            {"id": "supplier_active", "scope": "option", "type": "hard", "field": "supplier_active",
             "operator": "equals", "value": True, "description": "The supplier must be active."},
            {"id": "orders_stock", "scope": "option", "type": "hard", "field": "quantity_positive",
             "operator": "equals", "value": True, "description": "The option must order a positive quantity."},
            {"id": "value_review", "scope": "workflow", "type": "policy",
             "description": "Large order values need the Pharmacy Manager's attention."},
            {"id": "manager_approval", "scope": "workflow", "type": "approval",
             "description": "A Pharmacy Manager must approve before any purchase order is created; "
                            "this workflow never creates or changes orders."},
        ],
        "unknowns": unknowns,
        "evidence_mode": "live_pharmacy_data",
    }
