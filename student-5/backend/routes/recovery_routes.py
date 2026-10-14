"""Read-only Staffing Recovery evidence endpoint."""

from __future__ import annotations

from flask import Blueprint, jsonify

from authorization import require_manager
from services import recovery_service


recovery_blueprint = Blueprint(
    "recovery", __name__, url_prefix="/api/shifts")


@recovery_blueprint.get("/<int:shift_id>/recovery-evidence")
def recovery_evidence(shift_id: int):
    """Return authoritative evidence for a later Multi-Agent workflow."""
    require_manager()
    return jsonify(recovery_service.build_recovery_evidence(shift_id))
