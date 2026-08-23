"""Staff REST endpoints for the Student 5 backend/API microservice."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from services import staff_service
from validation import require_fields, require_json

staff_blueprint = Blueprint("staff", __name__, url_prefix="/api/staff")


@staff_blueprint.get("")
@staff_blueprint.get("/")
def list_staff():
    """GET /api/staff — list staff, optionally filtered."""
    records = staff_service.list_staff(
        department=request.args.get("department"),
        role=request.args.get("role"),
        availability_status=request.args.get("availability_status"),
    )
    return jsonify({"count": len(records), "staff": records})


@staff_blueprint.get("/search")
def search_staff():
    """GET /api/staff/search — free-text search across staff records."""
    records = staff_service.search_staff(
        query=request.args.get("q"),
        department=request.args.get("department"),
        role=request.args.get("role"),
        availability_status=request.args.get("availability_status"),
    )
    return jsonify({
        "query": request.args.get("q"),
        "count": len(records),
        "staff": records,
    })


@staff_blueprint.put("/<int:staff_id>/availability")
def update_availability(staff_id: int):
    """PUT /api/staff/<id>/availability — set a staff member's availability."""
    payload = require_json(request.get_json(silent=True))
    require_fields(payload, ("availability_status",))
    record = staff_service.update_availability(staff_id, payload["availability_status"])
    return jsonify({"message": "Availability updated.", "staff": record})
