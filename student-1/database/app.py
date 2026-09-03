# Database App for the Patient & Admissions Management System
# Creation date: 30/08/2026

# This is the only process that opens patients.db
# Sits on Port 6100

import os
import sqlite3

from flask import Flask, jsonify, request

import database.database as database
import database.init_database as init_database
from database.database import DataError

app = Flask(__name__)
app.teardown_appcontext(database.close_db)

PORT = int(os.getenv("PORT", "6100"))
HOST = os.getenv("HOST", "0.0.0.0")


def ensure_database_ready():
    db_path = database.DB_PATH
    if not db_path.exists():
        init_database.build(db_path)
        return

    try:
        conn = sqlite3.connect(db_path)
        table_names = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('patients', 'admissions')"
        ).fetchall()
        conn.close()
        if len(table_names) < 2:
            init_database.build(db_path)
    except sqlite3.DatabaseError:
        init_database.build(db_path)


ensure_database_ready()

# Response Helpers
def successResponse(data, status=200):
    response = jsonify(data)
    response.status_code = status
    return response

@app.errorhandler(DataError)
def handleDataError(error):
    return successResponse({"error": str(error)}, status=400)

@app.errorhandler(404)
def handleNotFound(error):
    return successResponse({"error": "Not Found"}, status=404)

# Resource definitions
# Generic CRUD functions refer to this data for each resource rather than hardcoding table names and columns in each route.
RESOURCES = {
    "patients": {
        "table": "patients",
        "pk": "patient_id",
        "columns": [
            "patient_id",
            "p_title",
            "p_first_name",
            "p_last_name",
            "p_date_of_birth",
            "p_assigned_sex",
            "p_mobile",
            "p_method_of_contact",
            "p_middle_name",
            "p_preferred_name",
            "p_maiden_name",
            "p_previous_last_name",
            "p_international_visitor",
            "p_email_address",
            "p_landline",
            "p_marital_status",
            "p_first_nations_heritage",
            "p_language_assistance",
            "patient_status",
            "created_at",
            "updated_at",
            "deactivated_at",
        ],
        "required": [
            "p_title",
            "p_first_name",
            "p_last_name",
            "p_date_of_birth",
            "p_assigned_sex",
            "p_mobile",
            "p_marital_status",
            "p_first_nations_heritage",
            "patient_status",
            "created_at",
            "updated_at",
        ],
        "filters": {
            "patient_status": "patient_status",
            "p_last_name": "p_last_name",
            "p_first_name": "p_first_name",
        },
        "order": "patient_id",
    },
    "patient-addresses": {
        "table": "patient_addresses",
        "pk": "address_id",
        "columns": [
            "address_id",
            "patient_id",
            "address_street",
            "address_suburb",
            "address_state",
            "address_postcode",
            "address_is_primary",
        ],
        "required": [
            "patient_id",
            "address_street",
            "address_suburb",
            "address_state",
            "address_postcode",
        ],
        "filters": {
            "patient_id": "patient_id",
            "address_state": "address_state",
            "address_is_primary": "address_is_primary",
        },
        "order": "address_id",
    },
    "patient-medical-information": {
        "table": "patient_medical_information",
        "pk": "insurance_id",
        "columns": [
            "insurance_id",
            "patient_id",
            "medicare_number",
            "medicare_individual_reference_number",
            "medicare_expiry_date",
            "private_insurance",
            "p_centrelink_number",
        ],
        "required": [
            "patient_id",
            "medicare_number",
            "medicare_individual_reference_number",
            "medicare_expiry_date",
        ],
        "filters": {
            "patient_id": "patient_id",
            "private_insurance": "private_insurance",
        },
        "order": "insurance_id",
    },
    "patient-contacts": {
        "table": "patient_contacts",
        "pk": "contact_id",
        "columns": [
            "contact_id",
            "patient_id",
            "contact_primary",
            "contact_first_name",
            "contact_last_name",
            "contact_date_of_birth",
            "contact_relationship",
            "contact_address_same_as_patient",
            "contact_address",
            "contact_mobile",
            "contact_landline",
            "contact_email",
        ],
        "required": [
            "patient_id",
            "contact_first_name",
            "contact_last_name",
            "contact_date_of_birth",
            "contact_relationship",
            "contact_address",
            "contact_mobile",
        ],
        "filters": {
            "patient_id": "patient_id",
            "contact_primary": "contact_primary",
        },
        "order": "contact_id",
    },
    "patient-admin-notes": {
        "table": "patient_admin_notes",
        "pk": "note_id",
        "columns": [
            "note_id",
            "patient_id",
            "note_text",
            "created_at",
        ],
        "required": [
            "patient_id",
            "note_text",
            "created_at",
        ],
        "filters": {
            "patient_id": "patient_id",
        },
        "order": "note_id",
    },
    "admissions": {
        "table": "admissions",
        "pk": "admission_id",
        "columns": [
            "admission_id",
            "patient_id",
            "admission_date",
            "discharge_date",
            "admission_status",
        ],
        "required": [
            "patient_id",
            "admission_status",
        ],
        "filters": {
            "patient_id": "patient_id",
            "admission_status": "admission_status",
        },
        "order": "admission_id",
    },
}

# This helper checks whether the requested table/resource exists in the RESOURCES dictionary.
# If it does not exist, it raises a clear error so the API tells the caller the resource is unknown.
def get_resource_config(resource_name):
    resource = RESOURCES.get(resource_name)
    if not resource:
        raise DataError(f"Resource '{resource_name}' not found", status=404)
    return resource

# This helper looks up one single record by its unique ID.
# It builds a SQL query for the selected table and stops with a helpful error if no matching record exists.
# For soft-deleted records, we treat them as not available to normal API consumers.
def fetch_or_404(resource_name, record_id):
    resource = get_resource_config(resource_name)
    query = f"SELECT * FROM {resource['table']} WHERE {resource['pk']} = ?"
    if supports_soft_delete(resource):
        query += " AND deactivated_at IS NULL"

    record = database.query_db(query, (record_id,), one=True)

    if not record:
        raise DataError(f"{resource_name.replace('-', ' ').title()} with ID {record_id} not found", status=404)
    return dict(record)

# This helper converts database rows into plain Python dictionaries.
# SQLite rows are returned in a row-object format, but JSON responses need normal dictionaries.
def serialize_rows(rows):
    return [dict(row) for row in rows]

# This helper checks whether a table supports soft deletion.
# For legal and clinical reasons, a record should be marked inactive instead of being permanently removed.
def supports_soft_delete(resource):
    return "deactivated_at" in resource.get("columns", [])

# This helper checks whether the incoming JSON is valid for the chosen table.
# It blocks bad columns, ensures required fields are present, and prevents empty update requests.
def validate_payload(resource, payload, mode="create"):
    if not isinstance(payload, dict):
        raise DataError("Request body must be a JSON object", status=400)

    allowed_fields = set(resource["columns"])
    invalid_fields = [key for key in payload if key not in allowed_fields]
    if invalid_fields:
        raise DataError(f"Invalid field(s): {', '.join(invalid_fields)}", status=400)

    if mode == "create":
        required = resource.get("required", [])
        missing = [field for field in required if field not in payload or payload[field] in (None, "")]
        if missing:
            raise DataError(f"Missing required field(s): {', '.join(missing)}", status=400)

    if mode == "update":
        if not payload:
            raise DataError("No fields supplied to update", status=400)

    return payload

@app.get("/health")
@app.get("/api/health")
def health_check():
    return successResponse({"status": "ok", "service": "student-1-database"})

# This route lists records in a resource table.
# By default it shows only active records, but an admin can request an explicit includeInactive=true flag to see deactivated ones for auditing.
@app.route("/api/<resource_name>", methods=["GET"])
def list_resource(resource_name):
    resource = get_resource_config(resource_name)
    query = f"SELECT * FROM {resource['table']}"
    params = []
    filters = []

    include_inactive = request.args.get("includeInactive", "false").lower() == "true"

    if supports_soft_delete(resource) and not include_inactive:
        filters.append("deactivated_at IS NULL")

    for query_key, column in resource.get("filters", {}).items():
        if query_key in request.args:
            filters.append(f"{column} = ?")
            params.append(request.args[query_key])

    if filters:
        query += " WHERE " + " AND ".join(filters)

    query += f" ORDER BY {resource['order']}"
    records = database.query_db(query, tuple(params))
    return successResponse(serialize_rows(records))

# This route creates a new record in the chosen table.
# It accepts JSON, checks the payload is valid, and inserts the data using the table's column list.
@app.route("/api/<resource_name>", methods=["POST"])
def create_resource(resource_name):
    resource = get_resource_config(resource_name)
    payload = request.get_json(silent=True) or {}
    cleaned = validate_payload(resource, payload, mode="create")

    columns = list(cleaned.keys())
    placeholders = ", ".join("?" for _ in columns)
    query = f"INSERT INTO {resource['table']} ({', '.join(columns)}) VALUES ({placeholders})"
    values = [cleaned[column] for column in columns]

    last_id, _ = database.write_db(query, tuple(values))
    record = database.query_db(
        f"SELECT * FROM {resource['table']} WHERE {resource['pk']} = ?",
        (last_id,),
        one=True,
    )
    return successResponse(dict(record), status=201)

# This route fetches one specific record by its ID.
# It is used when the user wants to view a single patient, admission, address, or note.
# Soft-deleted records are excluded from normal reads to keep the system aligned with clinical record rules, unless an admin deliberately requests them.
@app.route("/api/<resource_name>/<int:record_id>", methods=["GET"])
def show_resource(resource_name, record_id):
    resource = get_resource_config(resource_name)
    query = f"SELECT * FROM {resource['table']} WHERE {resource['pk']} = ?"
    include_inactive = request.args.get("includeInactive", "false").lower() == "true"
    if supports_soft_delete(resource) and not include_inactive:
        query += " AND deactivated_at IS NULL"

    record = database.query_db(query, (record_id,), one=True)
    if not record:
        raise DataError(f"{resource_name.replace('-', ' ').title()} with ID {record_id} not found", status=404)
    return successResponse(dict(record))

# This route updates an existing record.
# It allows the user to change one or many fields, while still blocking invalid field names and empty updates.
# If the record has already been soft-deleted, normal updates are blocked for safety and governance.
@app.route("/api/<resource_name>/<int:record_id>", methods=["PUT", "PATCH"])
def update_resource(resource_name, record_id):
    resource = get_resource_config(resource_name)
    fetch_or_404(resource_name, record_id)

    payload = request.get_json(silent=True) or {}
    cleaned = validate_payload(resource, payload, mode="update")

    if resource["pk"] in cleaned:
        cleaned.pop(resource["pk"])

    if not cleaned:
        raise DataError("No valid fields provided for update", status=400)

    assignments = ", ".join(f"{field} = ?" for field in cleaned.keys())
    values = [cleaned[field] for field in cleaned.keys()]
    values.append(record_id)

    query = f"UPDATE {resource['table']} SET {assignments} WHERE {resource['pk']} = ?"
    if supports_soft_delete(resource):
        query += " AND deactivated_at IS NULL"
    database.write_db(query, tuple(values))

    updated = database.query_db(
        f"SELECT * FROM {resource['table']} WHERE {resource['pk']} = ?",
        (record_id,),
        one=True,
    )
    return successResponse(dict(updated))

# This route does a soft delete instead of a permanent delete.
# In clinical systems, records must remain for auditing and legal accountability, so we mark them inactive instead of removing them.
@app.route("/api/<resource_name>/<int:record_id>", methods=["DELETE"])
def delete_resource(resource_name, record_id):
    resource = get_resource_config(resource_name)
    fetch_or_404(resource_name, record_id)

    if supports_soft_delete(resource):
        updates = ["deactivated_at = datetime('now')"]
        if resource["table"] == "patients":
            updates.append("patient_status = 'Inactive'")

        query = f"UPDATE {resource['table']} SET {', '.join(updates)} WHERE {resource['pk']} = ?"
        database.write_db(query, (record_id,))

        return successResponse({
            "deleted": False,
            "deactivated": True,
            "id": record_id,
            "message": "Record deactivated instead of being permanently deleted."
        })

    raise DataError(f"Resource '{resource_name}' does not support soft deletion", status=400)

# This route reactivates a previously deactivated record.
# It is used for administrative corrections or if a patient is reactivated after a temporary inactive period.
@app.route("/api/<resource_name>/<int:record_id>/restore", methods=["POST", "PUT", "PATCH"])
def restore_resource(resource_name, record_id):
    resource = get_resource_config(resource_name)

    if not supports_soft_delete(resource):
        raise DataError(f"Resource '{resource_name}' does not support soft deletion", status=400)

    existing = database.query_db(
        f"SELECT * FROM {resource['table']} WHERE {resource['pk']} = ?",
        (record_id,),
        one=True,
    )
    if not existing:
        raise DataError(f"{resource_name.replace('-', ' ').title()} with ID {record_id} not found", status=404)

    updates = ["deactivated_at = NULL"]
    if resource["table"] == "patients":
        updates.append("patient_status = 'Active'")

    query = f"UPDATE {resource['table']} SET {', '.join(updates)} WHERE {resource['pk']} = ?"
    database.write_db(query, (record_id,))

    restored = database.query_db(
        f"SELECT * FROM {resource['table']} WHERE {resource['pk']} = ?",
        (record_id,),
        one=True,
    )
    return successResponse({
        "restored": True,
        "id": record_id,
        "record": dict(restored),
        "message": "Record restored and returned to active status."
    })

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, 
            debug=os.getenv("FLASK_DEBUG", "1") == "1")