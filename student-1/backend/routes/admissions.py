# Admission API routes.
# These endpoints sit in front of the database microservice and expose
# the admissions table as a normal REST API for the backend app.

from flask import Blueprint, jsonify, request
import requests

from backend.config import DATABASE_URL

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
    payload, status = _db_call(
        "POST",
        "/api/admissions",
        payload=request.get_json(silent=True) or {},
    )
    return jsonify(payload), status

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

# The admissions table currently does not support deletion in any form, 
# so the following endpoints are commented out until the database service 
# is updated to support it.

# Soft deletes an admission record in the database service. 
# @admissions_bp.route("/<int:admission_id>", methods=["DELETE"])
# def delete_admission(admission_id):
#     payload, status = _db_call("DELETE", f"/admissions/{admission_id}")
#     return jsonify(payload), status

# Reactivates a previously deactivated admission record in the database service.
# @admissions_bp.route("/<int:admission_id>/restore", methods=["POST", "PUT", "PATCH"])
# def restore_admission(admission_id):
#     payload, status = _db_call(request.method, f"/admissions/{admission_id}/restore")
#     return jsonify(payload), status
