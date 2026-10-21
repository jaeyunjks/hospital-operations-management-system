"""Evidence for the shared Multi-Agent Server from one bed shortage case.

The shared server only understands a generic contract: an objective,
options with attributes and outcomes, structured constraints and unknowns.
This module projects one shortage case and its compatible options (the same
ones the coordinator sees on screen) into that contract.

The evidence is read-only. No bed is reserved here: a person reviews the
agents' proposal, and only an explicit coordinator decision through
PUT /api/shortage-cases/<id>/decide changes any data.
"""

from services import shortage_options

SCHEMA_VERSION = "1.0"
EVIDENCE_TYPE = "student-4-bed-shortage"
EVIDENCE_MODE = "authoritative_backend_snapshot"
MAX_BED_OPTIONS = 6
ESCALATE_ID = "escalate"

TASK_TEMPLATE = ("Recommend how to place the patient waiting in shortage case {case_id} "
                 "({urgency} urgency, needs a {category} bed{ward}).")

UNKNOWNS = (
    "Expected discharge times for beds that are currently occupied.",
    "When cleaning will finish for beds that are pending cleaning.",
    "Clinical needs of the patient beyond the recorded care category.",
)

RISKS = {
    "available_now": [],
    "pending_cleaning": ["The bed cannot be used until cleaning is completed."],
    "alternative_ward": ["The bed is outside the requested ward; the receiving ward must agree."],
}


def option_id(option):
    return "bed-{}".format(option["bed_id"]) if option.get("bed_id") else ESCALATE_ID


def bed_id_from_option(value):
    """Map an agent's option id back to a bed id, or None for escalation/unknown."""
    if isinstance(value, str) and value.startswith("bed-") and value[4:].isdigit():
        return int(value[4:])
    return None


def _label(option):
    if not option.get("bed_id"):
        return "Escalate to the on-call operations manager"
    return "Bed {} in room {}, {}".format(option["bed_number"], option["room_number"], option["ward"])


def _project(option, case):
    is_bed = bool(option.get("bed_id"))
    risks = list(RISKS.get(option["kind"], []))
    if not is_bed:
        risks.append("No bed is reserved; the patient keeps waiting until the manager acts.")
    if is_bed and case["urgency"] in ("High", "Critical") and option["kind"] != "available_now":
        risks.append("{} urgency but the bed is not usable in the requested ward right now."
                     .format(case["urgency"]))
    return {
        "id": option_id(option),
        "label": _label(option),
        "attributes": {
            "kind": option["kind"],
            "care_category_match": True,
            "same_ward": option["kind"] in ("available_now", "pending_cleaning")
            and is_bed and (not case.get("required_ward") or option.get("ward") == case["required_ward"]),
            "ready_now": option["kind"] in ("available_now", "alternative_ward"),
            "reserves_bed": is_bed,
            "ward": option.get("ward"),
            "room_type": option.get("type_name"),
        },
        "outcomes": {
            "metric_delta": 1 if is_bed else 0,
            "projected_value": 1 if is_bed else 0,
        },
        "risk_indicators": risks,
    }


def task_for(case):
    ward = " in {}".format(case["required_ward"]) if case.get("required_ward") else ""
    return TASK_TEMPLATE.format(case_id=case["case_id"], urgency=case["urgency"],
                                category=case["required_care_category"], ward=ward)


def build(case, options=None):
    """Return the evidence document for one shortage case.

    Bed options are capped at MAX_BED_OPTIONS (in on-screen order) so the
    local models get a bounded prompt; escalation is always included.
    """
    options = shortage_options.options_for(case) if options is None else options
    beds = [o for o in options if o.get("bed_id")]
    shown = beds[:MAX_BED_OPTIONS] + [o for o in options if not o.get("bed_id")]
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_type": EVIDENCE_TYPE,
        "evidence_mode": EVIDENCE_MODE,
        "case_id": case["case_id"],
        "scenario": "Room & Bed Management bed shortage case",
        "current_state": {
            "case": {key: case.get(key) for key in (
                "case_id", "status", "urgency", "required_care_category", "required_ward",
                "holding_location", "opened_at")},
            "compatible_beds_found": len(beds),
            "bed_options_included": min(len(beds), MAX_BED_OPTIONS),
        },
        "objective": {
            "metric": "patient_placed",
            "current": 0,
            "target": 1,
            "unit": "bed placement",
        },
        "options": [_project(o, case) for o in shown],
        "constraints": [
            {
                "id": "care_category_match",
                "scope": "option",
                "type": "hard",
                "field": "care_category_match",
                "operator": "equals",
                "value": True,
                "description": "The bed must match the patient's required care category.",
            },
            {
                "id": "prefer_ready_now",
                "scope": "workflow",
                "type": "policy",
                "description": "Prefer a bed that is usable now, especially for High or Critical urgency.",
            },
            {
                "id": "prefer_requested_ward",
                "scope": "workflow",
                "type": "policy",
                "description": "Prefer the requested ward; an alternative ward needs that ward's agreement.",
            },
            {
                "id": "human_approval",
                "scope": "workflow",
                "type": "approval",
                "description": "A bed coordinator must approve before any bed is reserved.",
            },
            {
                "id": "no_automatic_reservation",
                "scope": "workflow",
                "type": "safety",
                "description": "This workflow is read-only and cannot reserve or allocate a bed.",
            },
        ],
        "unknowns": list(UNKNOWNS),
        "provenance": {
            "sources": [
                {"endpoint": "GET /api/shortage-cases/<case_id>", "service": "student-4-backend"},
                {"endpoint": "GET /api/shortage-cases/<case_id>/options", "service": "student-4-backend"},
            ],
        },
    }
