"""Bed stays, operating theatre sessions, transfers and releases.

Every write passes through conflict.check_conflict() first, so a bed or
an operating table can never hold two active arrangements at once.
"""

from flask import Blueprint, request

import config
from auth import require_permission
from responses import ok, ApiError
from services import conflict
from services import database_client as dbc
from validation import check_choice, check_time_window, require_fields

bp = Blueprint("arrangements", __name__)

ACTIVE = ("Scheduled", "In Progress")


def _sync_bed(bed_id, arrangement_status):
    """Reflect an arrangement's status on its bed and the room."""
    bed = dbc.get_record("beds", bed_id)
    if arrangement_status == "In Progress":
        wanted = "occupied"
    elif arrangement_status == "Scheduled":
        wanted = "reserved" if bed["status"] == "available" else bed["status"]
    else:  # Completed or Cancelled
        others = [a for a in dbc.list_records("arrangements", bed_id=bed_id)
                  if a["status"] in ACTIVE]
        wanted = "occupied" if any(a["status"] == "In Progress" for a in others) else (
            "reserved" if others else "available")

    if bed["status"] != wanted and bed["status"] != "maintenance":
        dbc.update_record("beds", bed_id, {"status": wanted})
    conflict.refresh_room_status(bed["room_id"])


@bp.get("/arrangements")
def list_arrangements():
    return ok(dbc.list_records(
        "arrangements",
        bed_id=request.args.get("bed_id"),
        status=request.args.get("status"),
        purpose=request.args.get("purpose"),
        patient_id=request.args.get("patient_id"),
    ))


@bp.get("/arrangements/<int:arrangement_id>")
def get_arrangement(arrangement_id):
    record = dbc.get_record("arrangements", arrangement_id)
    record["bed"] = dbc.get_record("beds", record["bed_id"])
    record["room"] = dbc.get_record("rooms", record["bed"]["room_id"])
    return ok(record)


@bp.post("/arrangements")
def create_arrangement():
    """Create a bed stay or a theatre session."""
    user = require_permission("bed.allocate")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "bed_id", "patient_id", "purpose", "start_time")

    purpose = check_choice(payload["purpose"], config.PURPOSES, "purpose")
    status = check_choice(payload.get("status", "Scheduled"),
                          config.ARRANGEMENT_STATUSES, "status")
    check_time_window(payload["start_time"], payload.get("end_time"))

    if purpose == "Surgery":
        require_fields(payload, "procedure_name", "surgeon_name")

    bed, room = conflict.check_conflict(
        payload["bed_id"], payload["start_time"], payload.get("end_time")
    )

    # The care category always comes from the bed's own room type, never
    # from the caller, so an arrangement cannot claim a category the bed
    # does not actually provide.
    room_type = dbc.get_record("room-types", room["type_id"])

    record = dbc.create_record("arrangements", {
        "bed_id": payload["bed_id"],
        "patient_id": payload["patient_id"],
        "admission_id": payload.get("admission_id"),
        "purpose": purpose,
        "procedure_name": payload.get("procedure_name"),
        "surgeon_name": payload.get("surgeon_name"),
        "patient_requirements": payload.get("patient_requirements"),
        "care_category": room_type["care_category"],
        "start_time": payload["start_time"],
        "end_time": payload.get("end_time"),
        "status": status,
        "arranged_by": payload.get("arranged_by") or user["username"],
    })
    _sync_bed(record["bed_id"], record["status"])
    return ok(record, status=201)


@bp.put("/arrangements/<int:arrangement_id>")
def update_arrangement(arrangement_id):
    """Reschedule or start an arrangement, re-checking for clashes."""
    require_permission("bed.allocate")
    existing = dbc.get_record("arrangements", arrangement_id)
    if existing["status"] in ("Completed", "Cancelled"):
        raise ApiError("Arrangement {} is {} and cannot be modified".format(
            arrangement_id, existing["status"].lower()), status=409)

    payload = request.get_json(silent=True) or {}
    start = payload.get("start_time", existing["start_time"])
    end = payload.get("end_time", existing["end_time"])
    bed_id = payload.get("bed_id", existing["bed_id"])
    check_time_window(start, end)

    if "status" in payload:
        check_choice(payload["status"], config.ARRANGEMENT_STATUSES, "status")

    conflict.check_conflict(bed_id, start, end, exclude_id=arrangement_id)

    allowed = ("bed_id", "start_time", "end_time", "status", "procedure_name",
               "surgeon_name", "patient_requirements", "admission_id")
    updates = {k: v for k, v in payload.items() if k in allowed}
    record = dbc.update_record("arrangements", arrangement_id, updates)

    _sync_bed(record["bed_id"], record["status"])
    if bed_id != existing["bed_id"]:
        _sync_bed(existing["bed_id"], "Completed")
    return ok(record)


@bp.put("/arrangements/<int:arrangement_id>/release")
def release_arrangement(arrangement_id):
    """Record a discharge or the end of a theatre session."""
    user = require_permission("bed.release")
    payload = request.get_json(silent=True) or {}
    existing = dbc.get_record("arrangements", arrangement_id)

    if existing["status"] in ("Completed", "Cancelled"):
        raise ApiError("Arrangement {} is already {}".format(
            arrangement_id, existing["status"].lower()), status=409)

    record = dbc.update_record("arrangements", arrangement_id, {
        "status": "Completed",
        "end_time": payload.get("end_time") or existing["end_time"] or _now(),
    })
    _sync_bed(record["bed_id"], "Completed")
    return ok({"released": record, "released_by": user["username"]})


@bp.put("/arrangements/<int:arrangement_id>/cancel")
def cancel_arrangement(arrangement_id):
    """Soft delete. Arrangements are cancelled, never removed."""
    require_permission("bed.allocate")
    payload = request.get_json(silent=True) or {}
    existing = dbc.get_record("arrangements", arrangement_id)

    if existing["status"] == "In Progress":
        raise ApiError("Arrangement {} is in progress; release it instead".format(
            arrangement_id), status=409)
    if existing["status"] in ("Completed", "Cancelled"):
        raise ApiError("Arrangement {} is already {}".format(
            arrangement_id, existing["status"].lower()), status=409)

    record = dbc.update_record("arrangements", arrangement_id, {"status": "Cancelled"})
    _sync_bed(record["bed_id"], "Cancelled")
    return ok({"cancelled": record, "reason": payload.get("reason")})


@bp.post("/arrangements/<int:arrangement_id>/transfer")
def transfer_arrangement(arrangement_id):
    """Move a patient to a different bed.

    The current arrangement is completed and a new one is opened on the
    target bed, linked by transferred_from_id so the move stays
    traceable. Persona 3 (Bed & Room Coordinator) lists
    "Transfer patients between beds" as a routine task.
    """
    user = require_permission("bed.allocate")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "to_bed_id")

    existing = dbc.get_record("arrangements", arrangement_id)
    if existing["status"] != "In Progress":
        raise ApiError("Only an in-progress arrangement can be transferred", status=409)
    if int(payload["to_bed_id"]) == existing["bed_id"]:
        raise ApiError("Target bed is the same as the current bed")

    moved_at = payload.get("moved_at") or _now()
    _, room = conflict.check_conflict(payload["to_bed_id"], moved_at, None)
    room_type = dbc.get_record("room-types", room["type_id"])

    closed = dbc.update_record("arrangements", arrangement_id, {
        "status": "Completed", "end_time": moved_at,
    })
    _sync_bed(closed["bed_id"], "Completed")

    created = dbc.create_record("arrangements", {
        "bed_id": payload["to_bed_id"],
        "patient_id": existing["patient_id"],
        "admission_id": existing["admission_id"],
        "purpose": existing["purpose"],
        "procedure_name": existing["procedure_name"],
        "surgeon_name": existing["surgeon_name"],
        "patient_requirements": payload.get("reason") or existing["patient_requirements"],
        "care_category": room_type["care_category"],
        "start_time": moved_at,
        "status": "In Progress",
        "transferred_from_id": arrangement_id,
        "arranged_by": user["username"],
    })
    _sync_bed(created["bed_id"], "In Progress")

    return ok({"from": closed, "to": created, "moved_at": moved_at}, status=201)


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")
