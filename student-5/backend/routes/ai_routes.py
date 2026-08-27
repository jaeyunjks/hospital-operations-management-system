"""AI-ready endpoints for the Student 5 backend/API microservice.

These endpoints return deterministic rule-based results together with the
context an LLM will consume. No Ollama call is made until the AI integration
task (prompt artefact S5-AI-001).

Both are MANAGER-ONLY. Suggesting staff for a shift and summarising roster-wide
coverage are rostering operations, and they expose the whole workforce's
availability — an employee has no business calling either, whatever the UI
happens to show them. The guard belongs here rather than in the frontend for
the same reason it does everywhere else in this service: hiding a button is
not a permission model.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from authorization import require_manager
from errors import ValidationError
from services import ai_service
from validation import require_fields, require_json, validate_date

ai_blueprint = Blueprint("ai", __name__, url_prefix="/api/shifts")


@ai_blueprint.post("/suggest-staff")
def suggest_staff():
    """POST /api/shifts/suggest-staff — eligible candidate staff for a shift."""
    require_manager()
    payload = require_json(request.get_json(silent=True))
    require_fields(payload, ("shift_id",))

    shift_id = payload["shift_id"]
    if isinstance(shift_id, bool) or not isinstance(shift_id, int):
        raise ValidationError("'shift_id' must be an integer.",
                              {"field": "shift_id", "received": shift_id})

    limit = payload.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValidationError("'limit' must be an integer greater than zero.",
                              {"field": "limit", "received": limit})

    return jsonify(ai_service.suggest_staff(shift_id, limit=limit))


@ai_blueprint.post("/coverage-summary")
def coverage_summary():
    """POST /api/shifts/coverage-summary — coverage shaped for LLM summarisation."""
    require_manager()
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object.")

    shift_date = payload.get("shift_date")
    if shift_date:
        validate_date(shift_date)

    return jsonify(ai_service.coverage_summary(
        department=payload.get("department"), shift_date=shift_date
    ))
