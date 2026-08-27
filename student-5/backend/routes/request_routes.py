"""Unavailability request endpoints for the Student 5 backend/API microservice.

Authorization is enforced here, server-side. See authorization.py for the
Release 0 identity simulation and its documented limitation.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from authorization import require_manager, require_self, require_self_or_manager
from services import request_service
from validation import require_json

request_blueprint = Blueprint("requests", __name__, url_prefix="/api")


# --------------------------------------------------- employee self-service
@request_blueprint.get("/staff/<int:staff_id>/unavailability-requests")
def list_staff_requests(staff_id: int):
    """GET — one employee's own requests. Employee (self) or manager."""
    require_self_or_manager(staff_id)
    records = request_service.list_requests(staff_id=staff_id)
    return jsonify({"staff_id": staff_id, "count": len(records), "requests": records})


@request_blueprint.post("/staff/<int:staff_id>/unavailability-requests")
def create_staff_request(staff_id: int):
    """POST — employee submits their own request. Always starts Pending."""
    require_self(staff_id)
    payload = require_json(request.get_json(silent=True))
    record = request_service.create_request(staff_id, payload)
    return jsonify({"message": "Unavailability request submitted.",
                    "request": record}), 201


@request_blueprint.put("/staff/<int:staff_id>/unavailability-requests/<int:request_id>/cancel")
def cancel_staff_request(staff_id: int, request_id: int):
    """PUT — employee cancels their own Pending request."""
    require_self(staff_id)
    record = request_service.get_request(request_id)
    if record["staff_id"] != staff_id:
        # Never leak another employee's request through a mismatched path.
        require_self(record["staff_id"])
    return jsonify({"message": "Request cancelled.",
                    "request": request_service.cancel_request(request_id)})


# ------------------------------------------------------ manager queue
@request_blueprint.get("/unavailability-requests")
def list_requests():
    """GET — the manager review queue, optionally filtered by status."""
    require_manager()
    records = request_service.list_requests(
        request_status=request.args.get("request_status"))
    return jsonify({"count": len(records), "requests": records})


@request_blueprint.get("/unavailability-requests/<int:request_id>")
def get_request_detail(request_id: int):
    """GET — request detail plus the roster assignments it would affect.

    Affected assignments are derived on every read; nothing is stored.
    """
    require_manager()
    record = request_service.get_request(request_id)
    return jsonify({"request": record,
                    "affected_assignments": request_service.affected_assignments(request_id)})


@request_blueprint.put("/unavailability-requests/<int:request_id>/review")
def review_request(request_id: int):
    """PUT — manager approves or rejects a Pending request.

    Approval records the decision and returns the affected assignments for
    the manager to resolve. It never unassigns or replaces anyone.
    """
    require_manager()
    payload = require_json(request.get_json(silent=True))
    record = request_service.review_request(
        request_id, payload.get("decision"), payload.get("reviewed_by"))
    return jsonify({
        "message": f"Request {record['request_status'].lower()}.",
        "request": record,
        "affected_assignments": request_service.affected_assignments(request_id),
    })
