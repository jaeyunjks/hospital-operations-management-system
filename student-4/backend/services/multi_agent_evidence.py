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
# Kept small on purpose: the local Worker model (llama3.2:3b by default) must
# copy every option id, label and risk exactly, and fails semantic validation
# when it paraphrases. Three beds plus escalation keeps the prompt short.
MAX_BED_OPTIONS = 3
ESCALATE_ID = "escalate"

TASK_TEMPLATE = ("Recommend how to place the patient waiting in shortage case {case_id} "
                 "({urgency} urgency, needs a {category} bed{ward}).")

UNKNOWNS = (
    "Expected discharge times for beds that are currently occupied.",
    "When cleaning will finish for beds that are pending cleaning.",
    "Clinical needs of the patient beyond the recorded care category.",
)

PREFERENCES = (
    "Among eligible beds, prefer the first one listed (the on-screen order).",
    "If no bed option is eligible, report no_eligible_option; the coordinator then decides manually.",
)


def option_id(option):
    return "bed-{}".format(option["bed_id"]) if option.get("bed_id") else ESCALATE_ID


def bed_id_from_option(value):
    """Map an agent's option id back to a bed id, or None for escalation/unknown."""
    if isinstance(value, str) and value.startswith("bed-") and value[4:].isdigit():
        return int(value[4:])
    return None


def describe(option):
    """Human-readable option text for the screen (labels sent to the agents are the ids)."""
    if not option.get("bed_id"):
        return "Escalate to the on-call operations manager"
    return "Bed {} in room {}, {}".format(option["bed_number"], option["room_number"], option["ward"])


def _project(option, case):
    """One option in the shared contract.

    The label equals the id and risk_indicators stay empty, so there is no
    free text for the Worker to copy; every fact it needs is a short attribute
    (the same approach Student 5 uses for its staffing options).
    """
    is_bed = bool(option.get("bed_id"))
    same_ward = is_bed and (not case.get("required_ward") or option.get("ward") == case["required_ward"])
    return {
        "id": option_id(option),
        "label": option_id(option),
        # Every true/false attribute is exactly one hard constraint below.
        # Live Workers check each boolean they see as a hard constraint, so an
        # extra boolean (or a missing constraint for one) fails validation.
        "attributes": {
            "description": describe(option),
            "kind": option["kind"],
            "ward": option.get("ward"),
            "care_category_match": True,
            "in_requested_ward": bool(same_ward),
            "ready_now": option["kind"] in ("available_now", "alternative_ward"),
        },
        "outcomes": {
            "metric_delta": 1 if is_bed else 0,
            "projected_value": 1 if is_bed else 0,
        },
        "risk_indicators": [],
    }


def task_for(case):
    ward = " in {}".format(case["required_ward"]) if case.get("required_ward") else ""
    return TASK_TEMPLATE.format(case_id=case["case_id"], urgency=case["urgency"],
                                category=case["required_care_category"], ward=ward)


def build(case, options=None):
    """Return the evidence document for one shortage case.

    Only bed options are sent, capped at MAX_BED_OPTIONS (in on-screen
    order). Escalation is not an option for the agents: models judged it
    ineligible because it places nobody, which contradicted the evidence.
    It stays a manual choice on screen, and "no_eligible_option" from the
    Worker means the coordinator should escalate.
    """
    options = shortage_options.options_for(case) if options is None else options
    beds = [o for o in options if o.get("bed_id")]
    shown = beds[:MAX_BED_OPTIONS]
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_type": EVIDENCE_TYPE,
        "evidence_mode": EVIDENCE_MODE,
        "scenario": "Room & Bed Management bed shortage case",
        # Case facts are top-level keys on purpose: the shared Planner may only
        # list top-level keys as required evidence, and smaller local models
        # pick these names even when they sit inside a nested object.
        "case_id": case["case_id"],
        "status": case.get("status"),
        "urgency": case.get("urgency"),
        "required_care_category": case.get("required_care_category"),
        "required_ward": case.get("required_ward"),
        "holding_location": case.get("holding_location"),
        "opened_at": case.get("opened_at"),
        "compatible_beds_found": len(beds),
        "bed_options_included": min(len(beds), MAX_BED_OPTIONS),
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
                "id": "in_requested_ward",
                "scope": "option",
                "type": "hard",
                "field": "in_requested_ward",
                "operator": "equals",
                "value": True,
                "description": "The bed must be in the requested ward; another ward needs a manual decision.",
            },
            {
                "id": "ready_now",
                "scope": "option",
                "type": "hard",
                "field": "ready_now",
                "operator": "equals",
                "value": True,
                "description": "The bed must be usable now, not waiting for cleaning.",
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
        # Preferences are advisory text, not constraints: the Worker treated
        # policy constraints as hard checks and failed validation.
        "preferences": list(PREFERENCES),
        "unknowns": list(UNKNOWNS),
        "provenance": {
            "sources": [
                {"endpoint": "GET /api/shortage-cases/<case_id>", "service": "student-4-backend"},
                {"endpoint": "GET /api/shortage-cases/<case_id>/options", "service": "student-4-backend"},
            ],
        },
    }
