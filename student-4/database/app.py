"""
Database microservice — Room & Bed Management (Student 4).

Port 6400. This is the only process that opens rooms.db. It exposes
plain CRUD over the five owned tables plus three read-only views that
join across them. It contains no workflow rules: conflict detection,
AI calls and status transitions all live in the backend service.

Response envelope (team convention):
    {"success": true,  "data": ..., "error": null}
    {"success": false, "data": null, "error": "message"}
"""

import os

from flask import Flask, jsonify, request

import db
from db import DataError

app = Flask(__name__)
app.teardown_appcontext(db.close_connection)

PORT = int(os.getenv("PORT", "6400"))


# ---------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------
def ok(data, status=200):
    return jsonify({"success": True, "data": data, "error": None}), status


@app.errorhandler(DataError)
def handle_data_error(error):
    return jsonify({"success": False, "data": None, "error": error.message}), error.status


@app.errorhandler(404)
def handle_not_found(_error):
    return jsonify({"success": False, "data": None, "error": "Resource not found"}), 404


# ---------------------------------------------------------------------
# Resource definitions
#
# One entry per owned table. `columns` are the fields a client may write;
# `filters` are the query-string filters GET accepts. Keeping this as
# data rather than five near-identical route modules means a new column
# is a one-line change.
# ---------------------------------------------------------------------
RESOURCES = {
    "room-types": {
        "table": "room_types",
        "pk": "type_id",
        "columns": ["type_name", "care_category", "default_capacity",
                    "requires_monitoring", "description"],
        "required": ["type_name", "care_category", "default_capacity"],
        "filters": {"care_category": "care_category"},
        "order": "type_id",
    },
    "rooms": {
        "table": "rooms",
        "pk": "room_id",
        "columns": ["room_number", "ward", "floor", "type_id", "status", "notes"],
        "required": ["room_number", "ward", "floor", "type_id"],
        "filters": {"ward": "ward", "status": "status", "type_id": "type_id"},
        "order": "room_number",
    },
    "beds": {
        "table": "beds",
        "pk": "bed_id",
        "columns": ["room_id", "bed_number", "status"],
        "required": ["room_id", "bed_number"],
        "filters": {"room_id": "room_id", "status": "status"},
        "order": "bed_number",
    },
    "arrangements": {
        "table": "room_arrangements",
        "pk": "arrangement_id",
        "columns": ["bed_id", "patient_id", "admission_id", "purpose", "procedure_name",
                    "surgeon_name", "patient_requirements", "care_category", "start_time",
                    "end_time", "status", "transferred_from_id", "arranged_by"],
        "required": ["bed_id", "patient_id", "purpose", "care_category",
                     "start_time", "arranged_by"],
        "filters": {"bed_id": "bed_id", "status": "status", "purpose": "purpose",
                    "patient_id": "patient_id", "admission_id": "admission_id"},
        "order": "start_time DESC",
    },
    "shortage-cases": {
        "table": "shortage_cases",
        "pk": "case_id",
        "columns": ["patient_id", "admission_id", "required_care_category", "required_ward",
                    "urgency", "holding_location", "opened_at", "resolved_at", "status",
                    "chosen_option", "decision_reason", "resolved_bed_id", "opened_by",
                    "decided_by"],
        "required": ["patient_id", "required_care_category", "opened_at", "opened_by"],
        "filters": {"status": "status", "urgency": "urgency",
                    "required_care_category": "required_care_category"},
        "order": "opened_at DESC",
    },
}


def spec(resource):
    if resource not in RESOURCES:
        raise DataError("Unknown resource '{}'".format(resource), status=404)
    return RESOURCES[resource]


def fetch_or_404(meta, record_id):
    row = db.query_one(
        "SELECT * FROM {} WHERE {} = ?".format(meta["table"], meta["pk"]), (record_id,)
    )
    if row is None:
        raise DataError(
            "{} {} not found".format(meta["table"], record_id), status=404
        )
    return row


# ---------------------------------------------------------------------
# Generic CRUD routes
# ---------------------------------------------------------------------
@app.get("/db/<resource>")
def list_records(resource):
    meta = spec(resource)
    clauses, params = [], []
    for param, column in meta["filters"].items():
        value = request.args.get(param)
        if value:
            clauses.append("{} = ?".format(column))
            params.append(value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = "SELECT * FROM {}{} ORDER BY {}".format(meta["table"], where, meta["order"])
    return ok(db.query(sql, tuple(params)))


@app.get("/db/<resource>/<int:record_id>")
def get_record(resource, record_id):
    return ok(fetch_or_404(spec(resource), record_id))


@app.post("/db/<resource>")
def create_record(resource):
    meta = spec(resource)
    payload = request.get_json(silent=True) or {}

    missing = [f for f in meta["required"] if payload.get(f) in (None, "")]
    if missing:
        raise DataError("Missing required field(s): " + ", ".join(missing))

    fields = [c for c in meta["columns"] if c in payload]
    placeholders = ", ".join("?" for _ in fields)
    sql = "INSERT INTO {} ({}) VALUES ({})".format(
        meta["table"], ", ".join(fields), placeholders
    )
    new_id, _ = db.execute(sql, tuple(payload[f] for f in fields))
    return ok(fetch_or_404(meta, new_id), status=201)


@app.put("/db/<resource>/<int:record_id>")
def update_record(resource, record_id):
    meta = spec(resource)
    fetch_or_404(meta, record_id)
    payload = request.get_json(silent=True) or {}

    fields = [c for c in meta["columns"] if c in payload]
    if not fields:
        raise DataError("No updatable fields supplied")

    assignments = ", ".join("{} = ?".format(f) for f in fields)
    sql = "UPDATE {} SET {} WHERE {} = ?".format(meta["table"], assignments, meta["pk"])
    db.execute(sql, tuple(payload[f] for f in fields) + (record_id,))
    return ok(fetch_or_404(meta, record_id))


@app.delete("/db/<resource>/<int:record_id>")
def delete_record(resource, record_id):
    """Hard delete.

    Only room_types is ever hard deleted, and only when no room
    references it. Rooms and beds are retired through their status
    column and arrangements are cancelled, so the backend never calls
    this route for them — the guard below enforces that.
    """
    meta = spec(resource)
    fetch_or_404(meta, record_id)

    if meta["table"] != "room_types":
        raise DataError(
            "{} rows are retired through their status column, not deleted".format(
                meta["table"]
            )
        )

    in_use = db.query_one(
        "SELECT COUNT(*) AS n FROM rooms WHERE type_id = ?", (record_id,)
    )["n"]
    if in_use:
        raise DataError(
            "Room type {} is used by {} room(s) and cannot be deleted".format(
                record_id, in_use
            )
        )

    db.execute("DELETE FROM room_types WHERE type_id = ?", (record_id,))
    return ok({"deleted": record_id})


# ---------------------------------------------------------------------
# Read-only views
# ---------------------------------------------------------------------
@app.get("/db/views/availability")
def availability_view():
    """Every bed joined to its room and type, optionally filtered."""
    clauses, params = [], []
    if request.args.get("care_category"):
        clauses.append("rt.care_category = ?")
        params.append(request.args["care_category"])
    if request.args.get("bed_status"):
        clauses.append("b.status = ?")
        params.append(request.args["bed_status"])
    if request.args.get("ward"):
        clauses.append("r.ward = ?")
        params.append(request.args["ward"])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    return ok(db.query("""
        SELECT b.bed_id, b.bed_number, b.status AS bed_status,
               r.room_id, r.room_number, r.ward, r.floor, r.status AS room_status,
               r.notes, rt.type_id, rt.type_name, rt.care_category,
               rt.requires_monitoring, rt.default_capacity
        FROM beds b
        JOIN rooms r      ON r.room_id = b.room_id
        JOIN room_types rt ON rt.type_id = r.type_id
        {}
        ORDER BY rt.care_category, r.room_number, b.bed_number
    """.format(where), tuple(params)))


@app.get("/db/views/theatre-board")
def theatre_board_view():
    """Operating theatres with their current or next session."""
    return ok(db.query("""
        SELECT r.room_id, r.room_number, r.ward, r.status AS room_status,
               b.bed_id, b.status AS bed_status,
               a.arrangement_id, a.procedure_name, a.surgeon_name,
               a.start_time, a.end_time, a.status AS session_status,
               a.patient_id
        FROM rooms r
        JOIN room_types rt ON rt.type_id = r.type_id
        JOIN beds b        ON b.room_id = r.room_id
        LEFT JOIN room_arrangements a
               ON a.bed_id = b.bed_id
              AND a.status IN ('In Progress', 'Scheduled')
              AND a.arrangement_id = (
                    SELECT arrangement_id FROM room_arrangements
                    WHERE bed_id = b.bed_id AND status IN ('In Progress', 'Scheduled')
                    ORDER BY CASE status WHEN 'In Progress' THEN 0 ELSE 1 END, start_time
                    LIMIT 1)
        WHERE rt.type_name = 'Operating Theatre'
        ORDER BY r.room_number
    """))


@app.get("/db/views/occupancy-stats")
def occupancy_stats_view():
    """Counts the AI summary endpoint turns into plain language."""
    return ok({
        "by_care_category": db.query("""
            SELECT rt.care_category,
                   COUNT(*)                        AS total_beds,
                   SUM(b.status = 'available')     AS available,
                   SUM(b.status = 'reserved')      AS reserved,
                   SUM(b.status = 'occupied')      AS occupied,
                   SUM(b.status = 'maintenance')   AS maintenance
            FROM beds b
            JOIN rooms r       ON r.room_id = b.room_id
            JOIN room_types rt ON rt.type_id = r.type_id
            GROUP BY rt.care_category
        """),
        "by_ward": db.query("""
            SELECT r.ward,
                   COUNT(*)                    AS total_beds,
                   SUM(b.status = 'available') AS available,
                   SUM(b.status = 'occupied')  AS occupied
            FROM beds b JOIN rooms r ON r.room_id = b.room_id
            GROUP BY r.ward ORDER BY r.ward
        """),
        "theatres": db.query("""
            SELECT r.status, COUNT(*) AS rooms
            FROM rooms r JOIN room_types rt ON rt.type_id = r.type_id
            WHERE rt.type_name = 'Operating Theatre'
            GROUP BY r.status
        """),
        "open_shortages": db.query("""
            SELECT urgency, COUNT(*) AS cases FROM shortage_cases
            WHERE status IN ('Open', 'Option offered') GROUP BY urgency
        """),
    })


@app.get("/db/arrangements/overlaps")
def overlapping_arrangements():
    """Active arrangements that clash with a proposed time window.

    Backs the double-booking check. The backend decides what to do with
    the result; this route only reports the clash.
    """
    bed_id = request.args.get("bed_id")
    start = request.args.get("start")
    if not bed_id or not start:
        raise DataError("bed_id and start are required")
    end = request.args.get("end") or "9999"
    exclude = request.args.get("exclude_id") or -1

    return ok(db.query("""
        SELECT * FROM room_arrangements
        WHERE bed_id = ?
          AND arrangement_id <> ?
          AND status IN ('Scheduled', 'In Progress')
          AND start_time < ?
          AND COALESCE(end_time, '9999') > ?
        ORDER BY start_time
    """, (bed_id, exclude, end, start)))


@app.get("/health")
def health():
    counts = {t: db.query_one("SELECT COUNT(*) AS n FROM " + t)["n"]
              for t in ("room_types", "rooms", "beds", "room_arrangements", "shortage_cases")}
    return ok({"service": "student-4-database", "port": PORT, "records": counts})


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=PORT,
            debug=os.getenv("FLASK_DEBUG", "1") == "1")
