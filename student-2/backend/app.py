"""
app.py - Flask application entry point for the Clinical Staff Management service.

Wiring only. No business logic lives here: this file creates the Flask app,
attaches the five route blueprints under their URL prefixes, and runs the
development server on port 5200.

Each blueprint lives in its own file under backend/routes/ and exposes a module
-level Flask Blueprint object. Those files are still being built, so each import
is done defensively - a missing or broken route module is logged and skipped
rather than taking down the whole application.

Blueprint -> URL prefix map:
    clinical_records  -> /api/clinical-records
    consultations     -> /api/consultations
    care_tasks        -> /api/care-tasks
    surgery_requests  -> /api/surgery-requests
    ai_summary        -> /api/ai
"""

import importlib

from flask import Flask, jsonify


# (module filename, attribute holding the Blueprint, URL prefix)
BLUEPRINTS = [
    ("clinical_records", "clinical_records_bp", "/api/clinical-records"),
    ("consultations",    "consultations_bp",    "/api/consultations"),
    ("care_tasks",       "care_tasks_bp",       "/api/care-tasks"),
    ("surgery_requests", "surgery_requests_bp", "/api/surgery-requests"),
    ("ai_summary",       "ai_summary_bp",       "/api/ai"),
]


def _register_blueprints(app):
    """Attach every route blueprint we can load; skip the ones we can't."""
    for module_name, attr_name, url_prefix in BLUEPRINTS:
        try:
            # Import backend/routes/<module_name>.py
            module = importlib.import_module("routes.{}".format(module_name))
            blueprint = getattr(module, attr_name)
        except (ImportError, AttributeError) as exc:
            # Route file missing, has a syntax/import error, or hasn't defined
            # its Blueprint yet. Log and carry on so the rest still serves.
            app.logger.warning(
                "Skipping blueprint '%s' (%s): %s", module_name, url_prefix, exc
            )
            continue

        app.register_blueprint(blueprint, url_prefix=url_prefix)
        app.logger.info("Registered blueprint '%s' at %s", module_name, url_prefix)


def create_app():
    """Build and configure the Flask app."""
    app = Flask(__name__)

    # Simple health check so you can confirm the container is up without
    # depending on any route module having loaded.
    @app.get("/health")
    def health():
        return jsonify(status="ok", service="clinical-staff-management")

    _register_blueprints(app)
    return app


# Module-level app object so WSGI servers (e.g. gunicorn app:app) can find it.
app = create_app()


if __name__ == "__main__":
    # Development server only. Port 5200 is this service's assigned port.
    app.run(host="0.0.0.0", port=5200, debug=True)
