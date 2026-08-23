#!/usr/bin/env python3
"""HTTP data service for the Student 5 database microservice.

Staff & Shift Management. Exposes the repository layer over HTTP so the
backend/API microservice can reach the data without opening the SQLite file
itself. This preserves the microservice boundary described in
``student-5/README.md``:

    Backend/API Microservice --HTTP--> Database Microservice --> SQLite

This service is deliberately thin. It performs storage-level concerns only —
persistence, integrity errors, and row shaping. Application rules (coverage
calculation, staff suggestion, request validation) belong to the backend.

Usage:
    python3 service.py                 # serves on port 5051
    DATABASE_SERVICE_PORT=6001 python3 service.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db           # noqa: E402
import repository   # noqa: E402

DEFAULT_PORT = 5051


def create_app() -> Flask:
    app = Flask(__name__)

    # ---------------------------------------------------------------- errors
    @app.errorhandler(sqlite3.IntegrityError)
    def handle_integrity_error(error: sqlite3.IntegrityError):
        """Constraint violations map to 409 so the backend can react to them."""
        return jsonify({"error": "integrity_error", "message": str(error)}), 409

    @app.errorhandler(404)
    def handle_not_found(error):
        return jsonify({"error": "not_found", "message": "Resource not found."}), 404

    @app.errorhandler(500)
    def handle_server_error(error):
        return jsonify({"error": "database_error", "message": "Database service failure."}), 500

    # ---------------------------------------------------------------- health
    @app.get("/health")
    def health():
        with db.get_connection() as connection:
            counts = {
                table: repository.count_rows(connection, table)
                for table in ("staff", "shift", "shift_assignment")
            }
        return jsonify({"status": "ok", "service": "student-5-database", "counts": counts})

    # ----------------------------------------------------------------- staff
    @app.get("/staff")
    def list_staff():
        with db.get_connection() as connection:
            return jsonify(repository.list_staff(
                connection,
                department=request.args.get("department"),
                role=request.args.get("role"),
                availability_status=request.args.get("availability_status"),
            ))

    @app.get("/staff/<int:staff_id>")
    def get_staff(staff_id: int):
        with db.get_connection() as connection:
            record = repository.get_staff(connection, staff_id)
        if record is None:
            return jsonify({"error": "not_found", "message": f"Staff {staff_id} not found."}), 404
        return jsonify(record)

    @app.post("/staff")
    def create_staff():
        payload = request.get_json(silent=True) or {}
        with db.get_connection() as connection:
            staff_id = repository.create_staff(connection, **payload)
            record = repository.get_staff(connection, staff_id)
        return jsonify(record), 201

    @app.patch("/staff/<int:staff_id>")
    def update_staff(staff_id: int):
        payload = request.get_json(silent=True) or {}
        with db.get_connection() as connection:
            if repository.get_staff(connection, staff_id) is None:
                return jsonify({"error": "not_found", "message": f"Staff {staff_id} not found."}), 404
            repository.update_staff(connection, staff_id, **payload)
            record = repository.get_staff(connection, staff_id)
        return jsonify(record)

    @app.delete("/staff/<int:staff_id>")
    def delete_staff(staff_id: int):
        with db.get_connection() as connection:
            deleted = repository.delete_staff(connection, staff_id)
        if not deleted:
            return jsonify({"error": "not_found", "message": f"Staff {staff_id} not found."}), 404
        return "", 204

    @app.get("/staff/<int:staff_id>/shifts")
    def shifts_for_staff(staff_id: int):
        with db.get_connection() as connection:
            return jsonify(repository.list_shifts_for_staff(connection, staff_id))

    # ---------------------------------------------------------------- shifts
    @app.get("/shifts")
    def list_shifts():
        with db.get_connection() as connection:
            return jsonify(repository.list_shifts(
                connection,
                department=request.args.get("department"),
                shift_date=request.args.get("shift_date"),
                shift_status=request.args.get("shift_status"),
            ))

    @app.get("/shifts/<int:shift_id>")
    def get_shift(shift_id: int):
        with db.get_connection() as connection:
            record = repository.get_shift(connection, shift_id)
        if record is None:
            return jsonify({"error": "not_found", "message": f"Shift {shift_id} not found."}), 404
        return jsonify(record)

    @app.post("/shifts")
    def create_shift():
        payload = request.get_json(silent=True) or {}
        with db.get_connection() as connection:
            shift_id = repository.create_shift(connection, **payload)
            record = repository.get_shift(connection, shift_id)
        return jsonify(record), 201

    @app.patch("/shifts/<int:shift_id>")
    def update_shift(shift_id: int):
        payload = request.get_json(silent=True) or {}
        with db.get_connection() as connection:
            if repository.get_shift(connection, shift_id) is None:
                return jsonify({"error": "not_found", "message": f"Shift {shift_id} not found."}), 404
            repository.update_shift(connection, shift_id, **payload)
            record = repository.get_shift(connection, shift_id)
        return jsonify(record)

    @app.delete("/shifts/<int:shift_id>")
    def delete_shift(shift_id: int):
        with db.get_connection() as connection:
            deleted = repository.delete_shift(connection, shift_id)
        if not deleted:
            return jsonify({"error": "not_found", "message": f"Shift {shift_id} not found."}), 404
        return "", 204

    @app.get("/shifts/<int:shift_id>/staff")
    def staff_for_shift(shift_id: int):
        with db.get_connection() as connection:
            return jsonify(repository.list_staff_for_shift(connection, shift_id))

    # ----------------------------------------------------------- assignments
    @app.get("/assignments")
    def list_assignments():
        shift_id = request.args.get("shift_id", type=int)
        staff_id = request.args.get("staff_id", type=int)
        with db.get_connection() as connection:
            return jsonify(repository.list_assignments(
                connection,
                shift_id=shift_id,
                staff_id=staff_id,
                assignment_status=request.args.get("assignment_status"),
            ))

    @app.get("/assignments/<int:assignment_id>")
    def get_assignment(assignment_id: int):
        with db.get_connection() as connection:
            record = repository.get_assignment(connection, assignment_id)
        if record is None:
            return jsonify({"error": "not_found",
                            "message": f"Assignment {assignment_id} not found."}), 404
        return jsonify(record)

    @app.post("/assignments")
    def create_assignment():
        payload = request.get_json(silent=True) or {}
        with db.get_connection() as connection:
            assignment_id = repository.create_assignment(connection, **payload)
            record = repository.get_assignment(connection, assignment_id)
        return jsonify(record), 201

    @app.patch("/assignments/<int:assignment_id>")
    def update_assignment(assignment_id: int):
        payload = request.get_json(silent=True) or {}
        with db.get_connection() as connection:
            if repository.get_assignment(connection, assignment_id) is None:
                return jsonify({"error": "not_found",
                                "message": f"Assignment {assignment_id} not found."}), 404
            repository.update_assignment(connection, assignment_id, **payload)
            record = repository.get_assignment(connection, assignment_id)
        return jsonify(record)

    @app.delete("/assignments/<int:assignment_id>")
    def delete_assignment(assignment_id: int):
        with db.get_connection() as connection:
            deleted = repository.delete_assignment(connection, assignment_id)
        if not deleted:
            return jsonify({"error": "not_found",
                            "message": f"Assignment {assignment_id} not found."}), 404
        return "", 204

    return app


if __name__ == "__main__":
    port = int(os.environ.get("DATABASE_SERVICE_PORT", DEFAULT_PORT))
    create_app().run(host="0.0.0.0", port=port, debug=False)
