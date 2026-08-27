"""Assignment REST endpoints for the Student 5 backend/API microservice."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from authorization import require_manager
from services import assignment_service, eligibility_service
from validation import require_fields, require_json

assignment_blueprint = Blueprint("assignments", __name__, url_prefix="/api/shifts")


@assignment_blueprint.get("/<int:shift_id>/assignments")
def list_assignments(shift_id: int):
    """GET /api/shifts/<id>/assignments — staff assigned to a shift."""
    require_manager()
    records = assignment_service.list_shift_assignments(shift_id)
    return jsonify({"shift_id": shift_id, "count": len(records), "assignments": records})


@assignment_blueprint.get("/<int:shift_id>/candidates")
def list_candidates(shift_id: int):
    """GET /api/shifts/<id>/candidates — staff evaluated against this shift.

    Returns everyone holding the required role, ELIGIBLE OR NOT, each carrying
    the deterministic reason they cannot be used. Blocked staff are annotated
    rather than filtered out so a manager can see why someone is unavailable
    instead of them silently vanishing from the list.

    This is the frontend's source of eligibility. The rules live in
    ``eligibility_service`` and are evaluated here, once, rather than being
    re-implemented against the same data on the other side of the boundary.
    """
    require_manager()
    result = eligibility_service.candidates_for_shift(shift_id)
    return jsonify({
        "shift_id": shift_id,
        "count": len(result["candidates"]),
        "eligible_count": result["eligible_count"],
        "already_assigned_staff_ids": result["already_assigned_staff_ids"],
        "candidates": result["candidates"],
    })


@assignment_blueprint.post("/<int:shift_id>/assign")
def assign_staff(shift_id: int):
    """POST /api/shifts/<id>/assign — assign a staff member to a shift."""
    require_manager()
    payload = require_json(request.get_json(silent=True))
    require_fields(payload, ("staff_id",))
    record = assignment_service.assign_staff(
        shift_id, payload["staff_id"], approved_by=payload.get("approved_by")
    )
    return jsonify({"message": "Staff assigned to shift.", "assignment": record}), 201


@assignment_blueprint.put("/<int:shift_id>/unassign")
def unassign_staff(shift_id: int):
    """PUT /api/shifts/<id>/unassign — withdraw a staff member from a shift.

    The assignment is cancelled rather than deleted, retaining the roster
    history. This is why the endpoint is a PUT rather than a DELETE.
    """
    require_manager()
    payload = require_json(request.get_json(silent=True))
    require_fields(payload, ("staff_id",))
    record = assignment_service.unassign_staff(shift_id, payload["staff_id"])
    return jsonify({"message": "Staff removed from shift.", "assignment": record})
