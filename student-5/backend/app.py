#!/usr/bin/env python3
"""Flask application for the Student 5 backend/API microservice.

Staff & Shift Management — application service layer.

    HTMX Frontend --HTTP--> Backend/API (this service) --HTTP--> Database service

The backend holds the application logic and validation. It never opens the
SQLite file: all data access crosses the HTTP boundary in ``database_client``.

Usage:
    python3 app.py                       # serves on port 5500
    BACKEND_PORT=5501 python3 app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify  # noqa: E402

from config import Config  # noqa: E402
from database_client import database_client  # noqa: E402
from errors import DatabaseServiceError, register_error_handlers  # noqa: E402
from routes.ai_routes import ai_blueprint  # noqa: E402
from routes.assignment_routes import assignment_blueprint  # noqa: E402
from routes.coverage_routes import coverage_blueprint  # noqa: E402
from routes.shift_routes import shift_blueprint  # noqa: E402
from routes.staff_routes import staff_blueprint  # noqa: E402

SERVICE_NAME = "student-5-backend"
FEATURE = "Staff & Shift Management"


def create_app(config_object: type = Config) -> Flask:
    """Application factory."""
    app = Flask(__name__)
    app.config.from_object(config_object)

    # Coverage and AI blueprints register their static paths before the
    # shift blueprint's <int:shift_id> rules, which keeps /api/shifts/coverage
    # and /api/shifts/suggest-staff unambiguous.
    app.register_blueprint(staff_blueprint)
    app.register_blueprint(coverage_blueprint)
    app.register_blueprint(ai_blueprint)
    app.register_blueprint(assignment_blueprint)
    app.register_blueprint(shift_blueprint)

    register_error_handlers(app)

    @app.get("/health")
    def health():
        """Liveness probe, including reachability of the database service."""
        try:
            database = database_client.health()
            database_status = "ok"
        except DatabaseServiceError as error:
            database, database_status = {"message": str(error)}, "unavailable"

        status_code = 200 if database_status == "ok" else 503
        return jsonify({
            "status": "ok" if database_status == "ok" else "degraded",
            "service": SERVICE_NAME,
            "feature": FEATURE,
            "ai_enabled": app.config["AI_ENABLED"],
            "database_service": {
                "url": app.config["DATABASE_SERVICE_URL"],
                "status": database_status,
                "detail": database,
            },
        }), status_code

    @app.get("/api")
    def api_index():
        """Machine-readable index of the endpoints this service exposes."""
        return jsonify({
            "service": SERVICE_NAME,
            "feature": FEATURE,
            "endpoints": {
                "staff": [
                    "GET    /api/staff",
                    "GET    /api/staff/search",
                    "PUT    /api/staff/<staff_id>/availability",
                ],
                "shifts": [
                    "GET    /api/shifts",
                    "POST   /api/shifts",
                    "GET    /api/shifts/<shift_id>",
                    "PUT    /api/shifts/<shift_id>",
                    "DELETE /api/shifts/<shift_id>",
                ],
                "assignments": [
                    "GET    /api/shifts/<shift_id>/assignments",
                    "POST   /api/shifts/<shift_id>/assign",
                    "PUT    /api/shifts/<shift_id>/unassign",
                ],
                "coverage": [
                    "GET    /api/shifts/coverage",
                ],
                "ai_ready": [
                    "POST   /api/shifts/suggest-staff",
                    "POST   /api/shifts/coverage-summary",
                ],
            },
        })

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=Config.PORT, debug=False)
