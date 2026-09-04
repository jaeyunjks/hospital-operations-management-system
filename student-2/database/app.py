"""
app.py - Database container for the Clinical Staff Management microservice.

This is a thin data layer. It exposes plain REST CRUD over the five tables in
schema.sql and NOTHING else:
  * no business rules (admission-active checks, auto-cancel, discharge flags)
  * no calls to other services
  * no soft-delete logic beyond the status columns already in the schema

All of that lives in the backend container. Only that backend talks to this
API; it is never exposed to anyone else directly.

Validation here is limited to: the request body is a JSON object, required
columns are present on POST, and unknown columns are rejected. Anything
stricter is the backend's job.

On startup it calls init_db() so the database file and tables exist before the
first request is served. Listens on port 6200.
"""

import os
import sqlite3

from flask import Flask, jsonify, request

from init_db import get_db_path, init_db

app = Flask(__name__)

# ------------------------------------------------------------
# Table definitions
# Per table: the primary-key column, every writable column, and which of
# those are required on POST. Primary keys are AUTOINCREMENT, so they are
# never in the writable/required sets. These mirror schema.sql exactly.
# ------------------------------------------------------------
TABLES = {
    "clinical_records": {
        "pk": "record_id",
        "columns": [
            "patient_id", "admission_id", "doctor_id",
            "assessment_notes", "diagnosis_summary", "care_plan",
            "status", "updated_after_discharge",
            "created_at", "updated_at",
        ],
        "required": ["patient_id", "admission_id", "doctor_id", "assessment_notes"],
    },
    "consultation_requests": {
        "pk": "request_id",
        "columns": [
            "clinical_record_id", "patient_id", "admission_id",
            "requesting_doctor_id", "specialist_id",
            "reason_for_request", "recommendation",
            "status", "requested_at", "completed_at",
        ],
        "required": [
            "clinical_record_id", "patient_id", "admission_id",
            "requesting_doctor_id", "specialist_id", "reason_for_request",
        ],
    },
    "care_tasks": {
        "pk": "task_id",
        "columns": [
            "clinical_record_id", "assigned_nurse_id",
            "task_description", "notes",
            "status", "due_at", "completed_at",
        ],
        "required": ["clinical_record_id", "assigned_nurse_id", "task_description"],
    },
    "surgery_requests": {
        "pk": "request_id",
        "columns": [
            "patient_id", "admission_id", "doctor_id",
            "procedure_type", "scheduled_at",
            "status", "created_at",
        ],
        "required": [
            "patient_id", "admission_id", "doctor_id",
            "procedure_type", "scheduled_at",
        ],
    },
    "ai_summaries": {
        "pk": "summary_id",
        "columns": [
            "admission_id", "patient_id",
            "summary_text", "model_used", "source_reference", "summary_scope",
            "generated_at", "reviewed_by_staff_id", "review_status",
        ],
        "required": ["admission_id", "patient_id", "summary_text"],
    },
}


# ------------------------------------------------------------
# Database helpers
# ------------------------------------------------------------
def get_conn():
    """Open a connection to the SQLite file (same path resolution as init_db)."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row):
    return {key: row[key] for key in row.keys()}


# ------------------------------------------------------------
# Request-body checking (type / required / unknown fields only)
# ------------------------------------------------------------
def get_json_object():
    """Return the request body as a dict, or (None, error_response)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "request body must be a JSON object"}), 400)
    return data, None


def check_columns(spec, data, require_all):
    """
    Validate field names against the table spec.
      * unknown columns -> error
      * if require_all, every column in spec["required"] must be present
    Returns an error string, or None if the body is acceptable.
    """
    unknown = [k for k in data if k not in spec["columns"]]
    if unknown:
        return f"unknown field(s): {', '.join(sorted(unknown))}"
    if require_all:
        missing = [c for c in spec["required"] if c not in data]
        if missing:
            return f"missing required field(s): {', '.join(missing)}"
    return None


# ------------------------------------------------------------
# CRUD route factory
# One set of five endpoints is registered per table.
# ------------------------------------------------------------
def register_routes(table, spec):
    pk = spec["pk"]

    def list_rows():
        conn = get_conn()
        try:
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY {pk}").fetchall()
            return jsonify([row_to_dict(r) for r in rows])
        finally:
            conn.close()

    def get_row(row_id):
        conn = get_conn()
        try:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {pk} = ?", (row_id,)
            ).fetchone()
            if row is None:
                return jsonify({"error": "not found"}), 404
            return jsonify(row_to_dict(row))
        finally:
            conn.close()

    def create_row():
        data, err = get_json_object()
        if err:
            return err
        problem = check_columns(spec, data, require_all=True)
        if problem:
            return jsonify({"error": problem}), 400

        cols = list(data.keys())
        placeholders = ", ".join("?" for _ in cols)
        conn = get_conn()
        try:
            cur = conn.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                [data[c] for c in cols],
            )
            conn.commit()
            new_id = cur.lastrowid
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {pk} = ?", (new_id,)
            ).fetchone()
            return jsonify(row_to_dict(row)), 201
        except sqlite3.IntegrityError as exc:
            # e.g. bad FK, failed CHECK constraint - report, don't interpret.
            return jsonify({"error": f"integrity error: {exc}"}), 400
        finally:
            conn.close()

    def update_row(row_id):
        data, err = get_json_object()
        if err:
            return err
        problem = check_columns(spec, data, require_all=False)
        if problem:
            return jsonify({"error": problem}), 400
        if not data:
            return jsonify({"error": "no fields to update"}), 400

        assignments = ", ".join(f"{c} = ?" for c in data)
        conn = get_conn()
        try:
            cur = conn.execute(
                f"UPDATE {table} SET {assignments} WHERE {pk} = ?",
                [data[c] for c in data] + [row_id],
            )
            conn.commit()
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
            row = conn.execute(
                f"SELECT * FROM {table} WHERE {pk} = ?", (row_id,)
            ).fetchone()
            return jsonify(row_to_dict(row))
        except sqlite3.IntegrityError as exc:
            return jsonify({"error": f"integrity error: {exc}"}), 400
        finally:
            conn.close()

    def delete_row(row_id):
        conn = get_conn()
        try:
            cur = conn.execute(f"DELETE FROM {table} WHERE {pk} = ?", (row_id,))
            conn.commit()
            if cur.rowcount == 0:
                return jsonify({"error": "not found"}), 404
            return jsonify({"deleted": row_id})
        except sqlite3.IntegrityError as exc:
            return jsonify({"error": f"integrity error: {exc}"}), 400
        finally:
            conn.close()

    # Unique endpoint names per table so Flask's registry stays happy.
    app.add_url_rule(f"/{table}", f"{table}_list", list_rows, methods=["GET"])
    app.add_url_rule(f"/{table}", f"{table}_create", create_row, methods=["POST"])
    app.add_url_rule(
        f"/{table}/<int:row_id>", f"{table}_get", get_row, methods=["GET"]
    )
    app.add_url_rule(
        f"/{table}/<int:row_id>", f"{table}_update", update_row, methods=["PUT"]
    )
    app.add_url_rule(
        f"/{table}/<int:row_id>", f"{table}_delete", delete_row, methods=["DELETE"]
    )


for _table, _spec in TABLES.items():
    register_routes(_table, _spec)


@app.get("/health")
def health():
    """Simple liveness check for the container."""
    return jsonify({"status": "ok"})


# Ensure the database and tables exist before the first request is served.
# init_db() is a no-op if the tables are already there.
init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "6200"))
    app.run(host="0.0.0.0", port=port)
