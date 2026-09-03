"""Bed shortage cases (Architecture v2.2, section 5.2).

When no compatible bed is available, the coordinator opens a case that
records the requirement, urgency and holding location. The service
offers compatible options; the coordinator chooses one and records the
reason. Reserving a bed against a case is the reserve step of
reserve -> allocate -> occupy.
"""

from datetime import datetime

from flask import Blueprint, request

import config
from auth import require_permission
from responses import ok, ApiError
from services import database_client as dbc
from services import suggestions
from validation import check_choice, require_fields

bp = Blueprint("shortages", __name__)


@bp.get("/shortage-cases")
def list_cases():
    return ok(dbc.list_records(
        "shortage-cases",
        status=request.args.get("status"),
        urgency=request.args.get("urgency"),
        required_care_category=request.args.get("care_category"),
    ))


@bp.get("/shortage-cases/<int:case_id>")
def get_case(case_id):
    return ok(dbc.get_record("shortage-cases", case_id))


@bp.post("/shortage-cases")
def open_case():
    user = require_permission("bed.allocate")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "patient_id", "required_care_category")
    check_choice(payload["required_care_category"], config.CARE_CATEGORIES,
                 "required_care_category")
    if "urgency" in payload:
        check_choice(payload["urgency"], config.URGENCIES, "urgency")

    return ok(dbc.create_record("shortage-cases", {
        "patient_id": payload["patient_id"],
        "admission_id": payload.get("admission_id"),
        "required_care_category": payload["required_care_category"],
        "required_ward": payload.get("required_ward"),
        "urgency": payload.get("urgency", "Medium"),
        "holding_location": payload.get("holding_location"),
        "opened_at": payload.get("opened_at") or _now(),
        "status": "Open",
        "opened_by": user["username"],
    }), status=201)


@bp.get("/shortage-cases/<int:case_id>/options")
def case_options(case_id):
    """Compatible operational options for an open case.

    Three kinds, in the order the architecture lists them: a bed that is
    free now, a bed pending cleaning, and a bed in an approved
    alternative ward. Escalation is always available as the last option.
    """
    case = dbc.get_record("shortage-cases", case_id)
    category = case["required_care_category"]
    options = []

    for row in dbc.availability(care_category=category, bed_status="available"):
        if row["room_status"] == "Available" or row["room_status"] == "In Use":
            same_ward = (not case["required_ward"]) or row["ward"] == case["required_ward"]
            options.append({
                "kind": "available_now" if same_ward else "alternative_ward",
                "bed_id": row["bed_id"], "bed_number": row["bed_number"],
                "room_number": row["room_number"], "ward": row["ward"],
                "type_name": row["type_name"],
                "note": "Free now" if same_ward else
                        "Alternative ward: {}".format(row["ward"]),
            })

    for row in dbc.availability(care_category=category):
        if row["room_status"] == "Cleaning":
            options.append({
                "kind": "pending_cleaning",
                "bed_id": row["bed_id"], "bed_number": row["bed_number"],
                "room_number": row["room_number"], "ward": row["ward"],
                "type_name": row["type_name"],
                "note": "Available once cleaning completes: {}".format(row["notes"] or ""),
            })

    options.append({
        "kind": "escalate", "bed_id": None,
        "note": "Escalate to the on-call operations manager",
    })

    order = {"available_now": 0, "pending_cleaning": 1, "alternative_ward": 2, "escalate": 3}
    options.sort(key=lambda o: order[o["kind"]])
    return ok({"case": case, "options": options})


@bp.put("/shortage-cases/<int:case_id>/decide")
def decide_case(case_id):
    """Record the coordinator's choice, with the mandatory reason.

    Choosing a bed reserves it. Allocation still requires creating an
    arrangement, so no bed is ever occupied by this route alone.
    """
    user = require_permission("bed.allocate")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "chosen_option", "decision_reason")

    case = dbc.get_record("shortage-cases", case_id)
    if case["status"] in ("Resolved", "Cancelled"):
        raise ApiError("Case {} is already {}".format(case_id, case["status"].lower()),
                       status=409)

    updates = {
        "chosen_option": payload["chosen_option"],
        "decision_reason": payload["decision_reason"],
        "decided_by": user["username"],
    }

    bed_id = payload.get("resolved_bed_id")
    if bed_id:
        bed = dbc.get_record("beds", bed_id)
        if bed["status"] not in ("available", "reserved"):
            raise ApiError("Bed {} is {} and cannot be reserved".format(
                bed["bed_number"], bed["status"]), status=409)
        dbc.update_record("beds", bed_id, {"status": "reserved"})
        updates["resolved_bed_id"] = bed_id
        updates["status"] = "Option offered"
    elif payload.get("escalate"):
        updates["status"] = "Escalated"
        updates["resolved_at"] = _now()
    else:
        updates["status"] = "Option offered"

    return ok(dbc.update_record("shortage-cases", case_id, updates))


@bp.put("/shortage-cases/<int:case_id>/resolve")
def resolve_case(case_id):
    """Close a case once the patient has actually been placed."""
    user = require_permission("bed.allocate")
    payload = request.get_json(silent=True) or {}
    case = dbc.get_record("shortage-cases", case_id)

    if case["status"] in ("Resolved", "Cancelled"):
        raise ApiError("Case {} is already {}".format(case_id, case["status"].lower()),
                       status=409)

    return ok(dbc.update_record("shortage-cases", case_id, {
        "status": "Resolved",
        "resolved_at": payload.get("resolved_at") or _now(),
        "resolved_bed_id": payload.get("resolved_bed_id") or case["resolved_bed_id"],
        "decision_reason": payload.get("decision_reason") or case["decision_reason"],
        "decided_by": case["decided_by"] or user["username"],
    }))


@bp.put("/shortage-cases/<int:case_id>/cancel")
def cancel_case(case_id):
    """Soft delete — cases are cancelled with a reason, never removed."""
    user = require_permission("bed.allocate")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "decision_reason")
    case = dbc.get_record("shortage-cases", case_id)

    if case["resolved_bed_id"]:
        bed = dbc.get_record("beds", case["resolved_bed_id"])
        if bed["status"] == "reserved":
            dbc.update_record("beds", bed["bed_id"], {"status": "available"})

    return ok(dbc.update_record("shortage-cases", case_id, {
        "status": "Cancelled",
        "resolved_at": _now(),
        "decision_reason": payload["decision_reason"],
        "decided_by": user["username"],
    }))


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")
