"""CRUD routes for room types, rooms and beds.

These three resources share the same shape, so they share a blueprint.
Deletion is soft everywhere except room_types, which may be removed only
when no room references it.
"""

from flask import Blueprint, request

import config
from auth import require_permission
from responses import ok, ApiError
from services import conflict
from services import database_client as dbc
from validation import check_choice, require_fields

bp = Blueprint("catalogue", __name__)


# --- room types -------------------------------------------------------
@bp.get("/room-types")
def list_room_types():
    return ok(dbc.list_records("room-types", care_category=request.args.get("care_category")))


@bp.get("/room-types/<int:type_id>")
def get_room_type(type_id):
    return ok(dbc.get_record("room-types", type_id))


@bp.post("/room-types")
def create_room_type():
    require_permission("room.configure")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "type_name", "care_category", "default_capacity")
    check_choice(payload["care_category"], config.CARE_CATEGORIES, "care_category")
    return ok(dbc.create_record("room-types", payload), status=201)


@bp.put("/room-types/<int:type_id>")
def update_room_type(type_id):
    require_permission("room.configure")
    payload = request.get_json(silent=True) or {}
    if "care_category" in payload:
        check_choice(payload["care_category"], config.CARE_CATEGORIES, "care_category")
    return ok(dbc.update_record("room-types", type_id, payload))


@bp.delete("/room-types/<int:type_id>")
def delete_room_type(type_id):
    """Hard delete, permitted only when no room uses this type."""
    require_permission("room.configure")
    return ok(dbc.delete_record("room-types", type_id))


# --- rooms ------------------------------------------------------------
@bp.get("/rooms")
def list_rooms():
    return ok(dbc.list_records(
        "rooms",
        ward=request.args.get("ward"),
        status=request.args.get("status"),
        type_id=request.args.get("type_id"),
    ))


@bp.get("/rooms/<int:room_id>")
def get_room(room_id):
    room = dbc.get_record("rooms", room_id)
    room["beds"] = dbc.list_records("beds", room_id=room_id)
    room["type"] = dbc.get_record("room-types", room["type_id"])
    return ok(room)


@bp.post("/rooms")
def create_room():
    require_permission("room.configure")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "room_number", "ward", "floor", "type_id")
    if "status" in payload:
        check_choice(payload["status"], config.ROOM_STATUSES, "status")
    return ok(dbc.create_record("rooms", payload), status=201)


@bp.put("/rooms/<int:room_id>")
def update_room(room_id):
    require_permission("room.configure")
    payload = request.get_json(silent=True) or {}
    if "status" in payload:
        check_choice(payload["status"], config.ROOM_STATUSES, "status")
    return ok(dbc.update_record("rooms", room_id, payload))


@bp.put("/rooms/<int:room_id>/status")
def set_room_status(room_id):
    """Mark a room In Use, Cleaning or Out of Service."""
    require_permission("room.configure")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "status")
    status = check_choice(payload["status"], config.ROOM_STATUSES, "status")

    if status == "Out of Service":
        occupied = [b for b in dbc.list_records("beds", room_id=room_id)
                    if b["status"] == "occupied"]
        if occupied:
            raise ApiError(
                "Room {} still has {} occupied bed(s); release them first".format(
                    room_id, len(occupied)),
                status=409,
            )
    return ok(dbc.update_record("rooms", room_id, {"status": status}))


@bp.delete("/rooms/<int:room_id>")
def retire_room(room_id):
    """Soft delete — the room is retired, never removed."""
    require_permission("room.configure")
    occupied = [b for b in dbc.list_records("beds", room_id=room_id)
                if b["status"] == "occupied"]
    if occupied:
        raise ApiError(
            "Room {} still has {} occupied bed(s); release them first".format(
                room_id, len(occupied)),
            status=409,
        )
    room = dbc.update_record("rooms", room_id, {"status": "Out of Service"})
    for bed in dbc.list_records("beds", room_id=room_id):
        if bed["status"] != "maintenance":
            dbc.update_record("beds", bed["bed_id"], {"status": "maintenance"})
    return ok({"retired": room, "note": "Room and its beds retired, records preserved"})


# --- beds -------------------------------------------------------------
@bp.get("/beds")
def list_beds():
    return ok(dbc.list_records(
        "beds", room_id=request.args.get("room_id"), status=request.args.get("status")
    ))


@bp.get("/beds/<int:bed_id>")
def get_bed(bed_id):
    bed = dbc.get_record("beds", bed_id)
    bed["room"] = dbc.get_record("rooms", bed["room_id"])
    bed["current"] = next(
        (a for a in dbc.list_records("arrangements", bed_id=bed_id, status="In Progress")),
        None,
    )
    return ok(bed)


@bp.post("/beds")
def create_bed():
    require_permission("room.configure")
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "room_id", "bed_number")
    if "status" in payload:
        check_choice(payload["status"], config.BED_STATUSES, "status")
    return ok(dbc.create_record("beds", payload), status=201)


@bp.put("/beds/<int:bed_id>")
def update_bed(bed_id):
    require_permission("room.configure")
    payload = request.get_json(silent=True) or {}
    if "status" in payload:
        check_choice(payload["status"], config.BED_STATUSES, "status")
        bed = dbc.get_record("beds", bed_id)
        if bed["status"] == "occupied" and payload["status"] != "occupied":
            raise ApiError(
                "Bed is occupied; release the arrangement instead of editing the status",
                status=409,
            )
    updated = dbc.update_record("beds", bed_id, payload)
    conflict.refresh_room_status(updated["room_id"])
    return ok(updated)


@bp.delete("/beds/<int:bed_id>")
def retire_bed(bed_id):
    """Soft delete — the bed goes to maintenance."""
    require_permission("room.configure")
    bed = dbc.get_record("beds", bed_id)
    if bed["status"] == "occupied":
        raise ApiError("Bed {} is occupied and cannot be retired".format(bed["bed_number"]),
                       status=409)
    updated = dbc.update_record("beds", bed_id, {"status": "maintenance"})
    conflict.refresh_room_status(updated["room_id"])
    return ok({"retired": updated, "note": "Bed set to maintenance, records preserved"})


# --- availability -----------------------------------------------------
@bp.get("/rooms/availability")
def availability():
    """Real-time availability filtered by care category and status."""
    return ok(dbc.availability(
        care_category=request.args.get("care_category"),
        bed_status=request.args.get("bed_status"),
        ward=request.args.get("ward"),
    ))
