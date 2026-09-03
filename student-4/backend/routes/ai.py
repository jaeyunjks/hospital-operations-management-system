"""AI-assisted endpoints.

Release 0 required flow:
    Frontend -> Backend/API -> Ollama -> approved LLM -> Backend -> Frontend

Both endpoints are advisory. Architecture v2.2 section 5.2 is explicit:
"AI never assigns a bed; an authorised employee approves the
allocation." Neither route writes room, bed or arrangement state.
"""

from flask import Blueprint, request

from responses import ok
from services import suggestions
from validation import require_fields

bp = Blueprint("ai", __name__)


@bp.post("/rooms/suggest")
def suggest_room():
    """Rank compatible beds for a patient's recorded requirements."""
    payload = request.get_json(silent=True) or {}
    require_fields(payload, "patient_requirements")

    result = suggestions.suggest_rooms(
        payload["patient_requirements"],
        ward=payload.get("ward"),
        limit=int(payload.get("limit", 3)),
    )
    return ok(result)


@bp.post("/rooms/occupancy-summary")
def occupancy_summary():
    """Plain-language summary of ward occupancy and theatre utilisation."""
    return ok(suggestions.occupancy_summary())
