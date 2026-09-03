# Admission API routes.
# These endpoints sit in front of the database microservice and expose
# the admissions table as a normal REST API for the backend app.

from datetime import date

from flask import Blueprint, jsonify, request
import requests

from backend.config import DATABASE_URL
from backend.services.emergency_admission import (
    assign_emergency_capacity,
    create_emergency_admission,
    create_provisional_patient,
    find_existing_patients,
)

admissions_bp = Blueprint("admissions", __name__, url_prefix="/api/admissions")
DB_BASE_URL = DATABASE_URL.rstrip("/")

# Sends a request to the database microservice and returns JSON plus status.
def _db_call(method, path, payload=None, params=None):
    url = f"{DB_BASE_URL}{path}"
    try:
        response = requests.request(
            method=method,
            url=url,
            json=payload,
            params=params,
            timeout=10,
        )

        try:
            data = response.json()
        except ValueError:
            data = {"message": response.text}

        return data, response.status_code
    except requests.exceptions.RequestException as exc:
        return {"error": f"Database service unavailable: {exc}"}, 503

# Admission API Routes
# -----------------------------------------------------------------------------

# Lists all admissions in the database, with optional query parameters for filtering.
@admissions_bp.route("", methods=["GET"])
def list_admissions():
    payload, status = _db_call("GET", "/api/admissions", params=request.args.to_dict())
    return jsonify(payload), status

# Creates a new admission record in the database.
@admissions_bp.route("", methods=["POST"])
def create_admission():
    payload = request.get_json(silent=True) or {}
    admission_date = payload.get("admission_date")
    if not admission_date or str(admission_date).strip().lower() in {"datetime('now')", 'datetime("now")'}:
        payload["admission_date"] = date.today().isoformat()
    payload, status = _db_call(
        "POST",
        "/api/admissions",
        payload=payload,
    )
    return jsonify(payload), status

# Creates a full emergency admission workflow using the Student-1 service-layer logic
# for provisional identity capture, duplicate review, and emergency capacity allocation.
# Clinical documentation and task assignment are intentionally excluded because that
# belongs to the Clinical Staff Management feature (Student-2).
@admissions_bp.route("/emergency", methods=["POST"])
def create_emergency_case():
    payload = request.get_json(silent=True) or {}
    identity = payload.get("identity") or {}

    if not identity:
        return jsonify({"error": "Emergency identity data is required."}), 400

    possible_matches = payload.get("possible_matches") or payload.get("candidate_patients") or []
    match_candidates = find_existing_patients(identity, possible_matches)
    provisional_patient = create_provisional_patient(identity)

    admission = create_emergency_admission(
        payload.get("patient_id"),
        arrival_time=payload.get("arrival_time"),
        priority=payload.get("priority", "Emergency"),
        data_quality_flag="provisional" if provisional_patient["requires_reconciliation"] else "confirmed",
        identifiers=identity,
    )

    admission_id = payload.get("admission_id") or "emergency-admission"
    capacity = assign_emergency_capacity(
        admission_id,
        capacity_id=payload.get("capacity_id", "unassigned"),
        assigned_to=payload.get("assigned_to", "emergency team"),
        reason=payload.get("capacity_reason", "Emergency priority"),
    )

    return jsonify({
        "possible_matches": match_candidates,
        "provisional_patient": provisional_patient,
        "admission": admission,
        "capacity": capacity,
        "identity_review_required": provisional_patient["requires_reconciliation"],
    })

# Retrieves a single admission record by its database ID.
@admissions_bp.route("/<int:admission_id>", methods=["GET"])
def get_admission(admission_id):
    payload, status = _db_call(
        "GET",
        f"/api/admissions/{admission_id}",
        params=request.args.to_dict(),
    )
    return jsonify(payload), status

# Updates one or multiple fields on an existing admission record in the database
@admissions_bp.route("/<int:admission_id>", methods=["PUT", "PATCH"])
def update_admission(admission_id):
    payload, status = _db_call(
        request.method,
        f"/api/admissions/{admission_id}",
        payload=request.get_json(silent=True) or {},
    )
    return jsonify(payload), status


@admissions_bp.route("/<int:admission_id>", methods=["DELETE"])
def delete_admission(admission_id):
    payload, status = _db_call("DELETE", f"/api/admissions/{admission_id}")
    return jsonify(payload), status

# Reactivates a previously deactivated admission record in the database service.
# @admissions_bp.route("/<int:admission_id>/restore", methods=["POST", "PUT", "PATCH"])
# def restore_admission(admission_id):
#     payload, status = _db_call(request.method, f"/admissions/{admission_id}/restore")
#     return jsonify(payload), status
