# Database App for the Patient & Admissions Management System
# Creation date: 30/08/2026

# This is the only process that opens patients.db
# Sits on Port 6100

import os
import re
import sqlite3
from datetime import date, datetime

from flask import Flask, jsonify, request

import database.database as database
import database.init_database as init_database
from database.database import DataError

app = Flask(__name__)
app.teardown_appcontext(database.close_db)

PORT = int(os.getenv("PORT", "6100"))
HOST = os.getenv("HOST", "0.0.0.0")

MERGE_PATIENT_FIELDS = (
    "p_title", "p_first_name", "p_last_name", "p_date_of_birth", "p_assigned_sex",
    "p_mobile", "p_method_of_contact", "p_middle_name", "p_preferred_name",
    "p_maiden_name", "p_previous_last_name", "p_international_visitor",
    "p_email_address", "p_landline", "p_marital_status", "p_first_nations_heritage",
    "p_language_assistance",
)
MERGE_ADDRESS_FIELDS = (
    "address_street", "address_suburb", "address_state", "address_postcode",
)
MERGE_MEDICAL_FIELDS = (
    "medicare_number", "medicare_individual_reference_number", "medicare_expiry_date",
    "private_insurance", "p_centrelink_number",
)


def merge_value_needs_review(value):
    text = "" if value is None else str(value).strip().casefold()
    return (
        not text
        or text in {"unknown", "unassigned", "not provided", "1900-01-01", "0000"}
        or (len(text) > 1 and text.isdigit() and set(text) == {"0"})
    )


def merge_field_needs_review(source_value, duplicate_value):
    source_text = "" if source_value is None else str(source_value).strip()
    duplicate_text = "" if duplicate_value is None else str(duplicate_value).strip()
    both_blank = not source_text and not duplicate_text
    source_only = bool(source_text) and not merge_value_needs_review(source_value) and not duplicate_text
    return source_value != duplicate_value and not both_blank and not source_only


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
        if len(table_names) >= 2:
            admission_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(admissions)").fetchall()
            }
            if "admission_end" not in admission_columns:
                conn.execute("ALTER TABLE admissions ADD COLUMN admission_end TEXT")
            patient_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(patients)").fetchall()
            }
            patient_migrations = {
                "emergency_override": "INTEGER NOT NULL DEFAULT 0 CHECK (emergency_override IN (0, 1))",
                "identity_review_status": "TEXT NOT NULL DEFAULT 'Not required' CHECK (identity_review_status IN ('Not required', 'Pending', 'Duplicate', 'Not duplicate'))",
                "identity_review_candidate_id": "INTEGER",
                "identity_reviewed_by": "TEXT",
                "identity_reviewed_at": "TEXT",
            }
            for column, definition in patient_migrations.items():
                if column not in patient_columns:
                    conn.execute(f"ALTER TABLE patients ADD COLUMN {column} {definition}")
            patient_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'patients'"
            ).fetchone()[0]
            if "'Merged'" not in patient_sql:
                updated_patient_sql = re.sub(
                    r"(patient_status\s+TEXT\s+NOT\s+NULL\s+DEFAULT\s+'Active'\s+CHECK\s*\(\s*patient_status\s+IN\s*\()(.*?)(\)\s*\))",
                    lambda match: f"{match.group(1)}{match.group(2)}, 'Merged'{match.group(3)}",
                    patient_sql,
                    count=1,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if updated_patient_sql == patient_sql:
                    raise sqlite3.DatabaseError("Could not migrate the patient_status constraint")
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(updated_patient_sql.replace("CREATE TABLE patients", "CREATE TABLE patients_status_migration", 1))
                conn.execute("INSERT INTO patients_status_migration SELECT * FROM patients")
                conn.execute("DROP TABLE patients")
                conn.execute("ALTER TABLE patients_status_migration RENAME TO patients")
                conn.commit()
                conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "UPDATE admissions SET admission_date = ? "
                "WHERE admission_date IS NULL OR lower(trim(admission_date)) IN (?, ?)",
                (date.today().isoformat(), "datetime('now')", 'datetime("now")'),
            )
            conn.execute(
                "UPDATE patient_admin_notes SET created_at = ? "
                "WHERE lower(trim(created_at)) IN (?, ?)",
                (datetime.now().isoformat(sep=" ", timespec="seconds"), "datetime('now')", 'datetime("now")'),
            )
            conn.commit()
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


@app.post("/api/patients/merge")
def merge_patient_profiles():
    payload = request.get_json(silent=True) or {}
    try:
        source_id = int(payload.get("source_id"))
        duplicate_id = int(payload.get("duplicate_id"))
    except (TypeError, ValueError):
        raise DataError("Select a Source and Duplicate profile", status=400) from None
    if source_id == duplicate_id:
        raise DataError("Source and Duplicate must be different profiles", status=400)

    source_values = payload.get("source") or {}
    reviewed_fields = set(payload.get("reviewed_fields") or [])
    reviewer = str(payload.get("reviewed_by") or "Unknown user")[:120]
    contact_ids = payload.get("contact_ids") or []
    admission_ids = payload.get("admission_ids") or []
    if not isinstance(contact_ids, list) or not isinstance(admission_ids, list):
        raise DataError("Contact and admission selections must be lists", status=400)

    connection = database.get_db()
    try:
        connection.execute("BEGIN IMMEDIATE")
        source = connection.execute(
            "SELECT * FROM patients WHERE patient_id = ? AND deactivated_at IS NULL", (source_id,)
        ).fetchone()
        duplicate = connection.execute(
            "SELECT * FROM patients WHERE patient_id = ? AND deactivated_at IS NULL", (duplicate_id,)
        ).fetchone()
        if not source or not duplicate:
            raise DataError("Both profiles must exist and be active", status=404)
        if duplicate["emergency_override"] != 1 or duplicate["identity_review_status"] != "Pending":
            raise DataError("The Duplicate profile is not awaiting emergency identity review", status=400)

        source_address = connection.execute(
            "SELECT * FROM patient_addresses WHERE patient_id = ? AND address_is_primary = 1 ORDER BY address_id LIMIT 1",
            (source_id,),
        ).fetchone()
        duplicate_address = connection.execute(
            "SELECT * FROM patient_addresses WHERE patient_id = ? AND address_is_primary = 1 ORDER BY address_id LIMIT 1",
            (duplicate_id,),
        ).fetchone()
        source_medical = connection.execute(
            "SELECT * FROM patient_medical_information WHERE patient_id = ? ORDER BY insurance_id LIMIT 1",
            (source_id,),
        ).fetchone()
        duplicate_medical = connection.execute(
            "SELECT * FROM patient_medical_information WHERE patient_id = ? ORDER BY insurance_id LIMIT 1",
            (duplicate_id,),
        ).fetchone()

        compared = (
            ("patient", MERGE_PATIENT_FIELDS, source, duplicate),
            ("address", MERGE_ADDRESS_FIELDS, source_address, duplicate_address),
            ("medical", MERGE_MEDICAL_FIELDS, source_medical, duplicate_medical),
        )
        missing_reviews = []
        for group, fields, source_row, duplicate_row in compared:
            for field in fields:
                source_value = source_row[field] if source_row else None
                duplicate_value = duplicate_row[field] if duplicate_row else None
                if merge_field_needs_review(source_value, duplicate_value):
                    key = f"{group}:{field}"
                    if key not in reviewed_fields:
                        missing_reviews.append(key)
        if missing_reviews:
            raise DataError(
                "Review every mismatched, empty, or default field before merging: " + ", ".join(missing_reviews),
                status=400,
            )

        patient_values = source_values.get("patient") or {}
        address_values = source_values.get("address") or {}
        medical_values = source_values.get("medical") or {}
        if any(field not in patient_values for field in MERGE_PATIENT_FIELDS):
            raise DataError("The resolved Source profile is incomplete", status=400)
        if any(field not in address_values for field in MERGE_ADDRESS_FIELDS):
            raise DataError("The resolved Source address is incomplete", status=400)
        if any(field not in medical_values for field in MERGE_MEDICAL_FIELDS):
            raise DataError("The resolved Source insurance information is incomplete", status=400)

        patient_assignments = ", ".join(f"{field} = ?" for field in MERGE_PATIENT_FIELDS)
        connection.execute(
            f"UPDATE patients SET {patient_assignments}, updated_at = datetime('now') WHERE patient_id = ?",
            [patient_values[field] for field in MERGE_PATIENT_FIELDS] + [source_id],
        )

        if source_address:
            connection.execute(
                "UPDATE patient_addresses SET " + ", ".join(f"{field} = ?" for field in MERGE_ADDRESS_FIELDS)
                + " WHERE address_id = ?",
                [address_values[field] for field in MERGE_ADDRESS_FIELDS] + [source_address["address_id"]],
            )
        else:
            connection.execute(
                "INSERT INTO patient_addresses (patient_id, " + ", ".join(MERGE_ADDRESS_FIELDS)
                + ", address_is_primary) VALUES (?, " + ", ".join("?" for _ in MERGE_ADDRESS_FIELDS) + ", 1)",
                [source_id] + [address_values[field] for field in MERGE_ADDRESS_FIELDS],
            )

        if source_medical:
            connection.execute(
                "UPDATE patient_medical_information SET " + ", ".join(f"{field} = ?" for field in MERGE_MEDICAL_FIELDS)
                + " WHERE insurance_id = ?",
                [medical_values[field] for field in MERGE_MEDICAL_FIELDS] + [source_medical["insurance_id"]],
            )
        else:
            connection.execute(
                "INSERT INTO patient_medical_information (patient_id, " + ", ".join(MERGE_MEDICAL_FIELDS)
                + ") VALUES (?, " + ", ".join("?" for _ in MERGE_MEDICAL_FIELDS) + ")",
                [source_id] + [medical_values[field] for field in MERGE_MEDICAL_FIELDS],
            )

        contact_columns = (
            "contact_primary", "contact_first_name", "contact_last_name", "contact_date_of_birth",
            "contact_relationship", "contact_address_same_as_patient", "contact_address",
            "contact_mobile", "contact_landline", "contact_email",
        )
        for contact_id in contact_ids:
            contact = connection.execute(
                "SELECT * FROM patient_contacts WHERE contact_id = ? AND patient_id = ?",
                (int(contact_id), duplicate_id),
            ).fetchone()
            if not contact:
                raise DataError("A selected emergency contact does not belong to the Duplicate profile", status=400)
            connection.execute(
                "INSERT INTO patient_contacts (patient_id, " + ", ".join(contact_columns)
                + ") VALUES (?, " + ", ".join("?" for _ in contact_columns) + ")",
                [source_id] + [contact[field] for field in contact_columns],
            )

        for admission_id in admission_ids:
            changed = connection.execute(
                "UPDATE admissions SET patient_id = ? WHERE admission_id = ? AND patient_id = ?",
                (source_id, int(admission_id), duplicate_id),
            ).rowcount
            if not changed:
                raise DataError("A selected admission does not belong to the Duplicate profile", status=400)

        now = datetime.now().isoformat(timespec="seconds")
        duplicate_notes = connection.execute(
            "SELECT note_text, created_at FROM patient_admin_notes WHERE patient_id = ? ORDER BY note_id",
            (duplicate_id,),
        ).fetchall()
        for note in duplicate_notes:
            connection.execute(
                "INSERT INTO patient_admin_notes (patient_id, note_text, created_at) VALUES (?, ?, ?)",
                (source_id, f"[Merged from duplicate MRN-{duplicate_id}] {note['note_text']}", note["created_at"]),
            )
        connection.execute(
            "INSERT INTO patient_admin_notes (patient_id, note_text, created_at) VALUES (?, ?, ?)",
            (source_id, f"PATIENT PROFILE {duplicate_id} MERGED INTO {source_id}", now),
        )
        connection.execute(
            "UPDATE patients SET patient_status = 'Merged', deactivated_at = ?, "
            "identity_review_status = 'Duplicate', identity_review_candidate_id = ?, "
            "identity_reviewed_by = ?, identity_reviewed_at = ?, updated_at = ? WHERE patient_id = ?",
            (now, source_id, reviewer, now, now, duplicate_id),
        )
        merged_source = connection.execute(
            "SELECT * FROM patients WHERE patient_id = ?", (source_id,)
        ).fetchone()
        connection.commit()
    except DataError:
        connection.rollback()
        raise
    except (ValueError, TypeError, sqlite3.IntegrityError) as error:
        connection.rollback()
        raise DataError(f"Merge could not be completed: {error}", status=400) from error
    except Exception:
        connection.rollback()
        raise

    return successResponse({"merged": True, "source": dict(merged_source)})

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
            "emergency_override",
            "identity_review_status",
            "identity_review_candidate_id",
            "identity_reviewed_by",
            "identity_reviewed_at",
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
            "admission_end",
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


def normalize_admission_payload(resource_name, payload):
    if resource_name != "admissions":
        return payload

    admission_date = payload.get("admission_date")
    if not admission_date or str(admission_date).strip().lower() in {"datetime('now')", 'datetime("now")'}:
        payload["admission_date"] = date.today().isoformat()
    validate_admission_interval(payload)
    if "admission_status" in payload:
        validate_admission_status(payload)
        if payload["admission_status"] == "Completed" and not payload.get("discharge_date"):
            payload["discharge_date"] = datetime.now().isoformat(timespec="minutes")
    return payload


def validate_admission_interval(payload):
    if not payload.get("admission_end"):
        return

    try:
        start = datetime.fromisoformat(str(payload.get("admission_date", "")))
        end = datetime.fromisoformat(str(payload["admission_end"]))
    except ValueError:
        raise DataError("Admission start and end must be valid date and time values", status=400) from None

    if start.tzinfo is not None or end.tzinfo is not None:
        raise DataError("Admission times must use the local time format", status=400)
    duration_seconds = (end - start).total_seconds()
    if (
        start.minute % 15
        or start.second
        or start.microsecond
        or duration_seconds < 900
        or duration_seconds % 900
    ):
        raise DataError("Admission times must use 15-minute blocks with a minimum duration of 15 minutes", status=400)


def validate_admission_status(payload):
    status = payload.get("admission_status")
    if status not in {"Pending", "Active", "Cancelled", "Completed"}:
        raise DataError("Select a valid admission status", status=400)

    try:
        start = datetime.fromisoformat(str(payload.get("admission_date", "")))
    except ValueError:
        raise DataError("Admission start must be a valid date and time value", status=400) from None

    if start.tzinfo is not None:
        raise DataError("Admission start must use the local time format", status=400)
    if status == "Pending" and start <= datetime.now():
        raise DataError("Pending admissions must be scheduled for a future date and time", status=400)
    if status in {"Active", "Completed"} and start > datetime.now():
        raise DataError("Active or completed admissions must have started", status=400)

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
    payload = normalize_admission_payload(resource_name, payload)
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
    existing = fetch_or_404(resource_name, record_id)

    payload = request.get_json(silent=True) or {}
    if resource_name == "admissions":
        updated_admission = {**existing, **payload}
        validate_admission_interval(updated_admission)
        if "admission_status" in payload:
            validate_admission_status(updated_admission)
            if payload["admission_status"] == "Completed" and not payload.get("discharge_date"):
                payload["discharge_date"] = datetime.now().isoformat(timespec="minutes")
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

    if resource_name in {"admissions", "patient-contacts"}:
        database.write_db(
            f"DELETE FROM {resource['table']} WHERE {resource['pk']} = ?",
            (record_id,),
        )
        return successResponse({"deleted": True, "id": record_id})

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