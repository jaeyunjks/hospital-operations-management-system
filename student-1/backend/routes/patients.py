# Patient API routes.
# These endpoints sit in front of the database microservice and expose
# the patients table as a normal REST API for the backend app.

from flask import Blueprint, jsonify, request
import requests

from backend.config import DATABASE_URL

patients_bp = Blueprint("patients", __name__, url_prefix="/api/patients")
DB_BASE_URL = DATABASE_URL.rstrip("/")

# Send a request to the database microservice and return JSON plus status
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

# Returns all active patients. Supports includeInactive=true for audit use.
@patients_bp.route("", methods=["GET"])
def list_patients():
    payload, status = _db_call("GET", "/api/patients", params=request.args.to_dict())
    return jsonify(payload), status

# Creates a new patient record in the database.
@patients_bp.route("", methods=["POST"])
def create_patient():
    payload, status = _db_call(
        "POST",
        "/api/patients",
        payload=request.get_json(silent=True) or {},
    )
    return jsonify(payload), status

# Retrieves a single patient record by its patient ID
@patients_bp.route("/<int:patient_id>", methods=["GET"])
def get_patient(patient_id):
    payload, status = _db_call(
        "GET",
        f"/api/patients/{patient_id}",
        params=request.args.to_dict(),
    )
    return jsonify(payload), status

# Updates one or more fields on an existing patient record.
@patients_bp.route("/<int:patient_id>", methods=["PUT", "PATCH"])
def update_patient(patient_id):
    payload, status = _db_call(
        request.method,
        f"/api/patients/{patient_id}",
        payload=request.get_json(silent=True) or {},
    )
    return jsonify(payload), status

# Soft delete a patient record in the database service.
@patients_bp.route("/<int:patient_id>", methods=["DELETE"])
def delete_patient(patient_id):
    payload, status = _db_call("DELETE", f"/api/patients/{patient_id}")
    return jsonify(payload), status

# Reactivate a previously deactivated patient record.
@patients_bp.route("/<int:patient_id>/restore", methods=["POST", "PUT", "PATCH"])
def restore_patient(patient_id):
    payload, status = _db_call(request.method, f"/api/patients/{patient_id}/restore")
    return jsonify(payload), status
