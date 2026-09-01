#!/usr/bin/env python3
"""Student 3 backend API for Pharmacy Operations.

The frontend calls this service. This service, in turn, calls the Student 3
database service over HTTP; it never opens the SQLite file directly.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, jsonify, request

DATABASE_SERVICE_URL = os.environ.get(
    "DATABASE_SERVICE_URL", "http://127.0.0.1:6300"
).rstrip("/")
PORT = int(os.environ.get("BACKEND_PORT", "5300"))

app = Flask(__name__)


class DatabaseServiceError(RuntimeError):
    """Raised when the Student 3 database service cannot satisfy a request."""


def database_get(path: str):
    """Read JSON from the Student 3 database service."""
    request_url = f"{DATABASE_SERVICE_URL}{path}"
    try:
        with urllib.request.urlopen(request_url, timeout=5) as response:
            return json.load(response)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise DatabaseServiceError(str(exc)) from exc


@app.get("/health")
def health():
    try:
        database_get("/health")
        database_status = "ok"
    except DatabaseServiceError:
        database_status = "unavailable"
    return jsonify({
        "status": "ok" if database_status == "ok" else "degraded",
        "service": "student-3-backend",
        "database_service": database_status,
    }), (200 if database_status == "ok" else 503)


@app.get("/api/staff")
def list_staff():
    """Expose staff data to the frontend through the backend boundary."""
    role = request.args.get("role")
    if role not in (None, "manager", "staff"):
        return jsonify({"error": "Invalid staff role"}), 400

    query = f"?{urllib.parse.urlencode({'role': role})}" if role else ""
    try:
        return jsonify({"staff": database_get(f"/staff{query}")})
    except DatabaseServiceError as exc:
        return jsonify({"error": "Database service unavailable", "detail": str(exc)}), 503


@app.get("/api/staff/<int:staff_id>")
def get_staff_member(staff_id: int):
    """Expose one staff record to the frontend through the backend boundary."""
    try:
        return jsonify({"staff": database_get(f"/staff/{staff_id}")})
    except DatabaseServiceError as exc:
        return jsonify({"error": "Staff member unavailable", "detail": str(exc)}), 503


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
