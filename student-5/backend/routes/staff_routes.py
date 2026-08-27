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
        employment_status=request.args.get("employment_status"),
    )
    return jsonify({"count": len(records), "staff": records})


@staff_blueprint.get("/<int:staff_id>")
def get_staff(staff_id: int):
    """GET /api/staff/<id> — retrieve one staff record."""
    return jsonify({"staff": staff_service.get_staff(staff_id)})


@staff_blueprint.get("/<int:staff_id>/weekly-availability")
def get_weekly_availability(staff_id: int):
    """GET /api/staff/<id>/weekly-availability — recurring weekly pattern."""
    periods = staff_service.get_weekly_availability(staff_id)
    return jsonify({"staff_id": staff_id, "count": len(periods), "periods": periods})


@staff_blueprint.put("/<int:staff_id>/weekly-availability")
def replace_weekly_availability(staff_id: int):
    """PUT /api/staff/<id>/weekly-availability — replace the whole pattern."""
    payload = require_json(request.get_json(silent=True))
    periods = staff_service.replace_weekly_availability(staff_id, payload.get("periods"))
    return jsonify({"message": "Weekly availability updated.",
                    "staff_id": staff_id, "count": len(periods), "periods": periods})


@staff_blueprint.get("/<int:staff_id>/shifts")
def list_staff_shifts(staff_id: int):
    """GET /api/staff/<id>/shifts — shifts this staff member is assigned to."""
    records = staff_service.list_staff_shifts(staff_id)
    return jsonify({"staff_id": staff_id, "count": len(records), "shifts": records})


@staff_blueprint.get("/search")
def search_staff():
    """GET /api/staff/search — free-text search across staff records."""
    records = staff_service.search_staff(
        query=request.args.get("q"),
        department=request.args.get("department"),
        role=request.args.get("role"),
        availability_status=request.args.get("availability_status"),
        employment_status=request.args.get("employment_status"),
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
