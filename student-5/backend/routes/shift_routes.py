"""Shift REST endpoints for the Student 5 backend/API microservice."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from authorization import require_manager
from services import shift_service
from validation import require_json

shift_blueprint = Blueprint("shifts", __name__, url_prefix="/api/shifts")


@shift_blueprint.get("")
@shift_blueprint.get("/")
def list_shifts():
    """GET /api/shifts — list shifts, optionally filtered."""
    require_manager()
    records = shift_service.list_shifts(
        department=request.args.get("department"),
        shift_date=request.args.get("shift_date"),
        shift_status=request.args.get("shift_status"),
    )
    return jsonify({"count": len(records), "shifts": records})


@shift_blueprint.post("")
@shift_blueprint.post("/")
def create_shift():
    """POST /api/shifts — create a shift."""
    require_manager()
    payload = require_json(request.get_json(silent=True))
    record = shift_service.create_shift(payload)
    return jsonify({"message": "Shift created.", "shift": record}), 201


@shift_blueprint.get("/<int:shift_id>")
def get_shift(shift_id: int):
    """GET /api/shifts/<id> — retrieve one shift."""
    require_manager()
    return jsonify({"shift": shift_service.get_shift(shift_id)})


@shift_blueprint.put("/<int:shift_id>")
def update_shift(shift_id: int):
    """PUT /api/shifts/<id> — update a shift."""
    require_manager()
    payload = require_json(request.get_json(silent=True))
    record = shift_service.update_shift(shift_id, payload)
    return jsonify({"message": "Shift updated.", "shift": record})


@shift_blueprint.delete("/<int:shift_id>")
def delete_shift(shift_id: int):
    """DELETE /api/shifts/<id> — delete a shift and its assignments."""
    require_manager()
    shift_service.delete_shift(shift_id)
    return jsonify({"message": f"Shift {shift_id} deleted."})
