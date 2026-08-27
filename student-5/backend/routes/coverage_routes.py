"""Staffing coverage endpoints for the Student 5 backend/API microservice."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from authorization import require_manager
from services import coverage_service

coverage_blueprint = Blueprint("coverage", __name__, url_prefix="/api/shifts")


@coverage_blueprint.get("/coverage")
def shift_coverage():
    """GET /api/shifts/coverage — required vs assigned staffing per shift."""
    require_manager()
    return jsonify(coverage_service.shift_coverage(
        department=request.args.get("department"),
        shift_date=request.args.get("shift_date"),
        shift_status=request.args.get("shift_status"),
    ))
