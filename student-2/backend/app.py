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
import os

from flask import Flask, jsonify, request


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

    # Several templates (care_tasks.html, consultation_queue.html,
    # consultation_form.html, patient_summary.html) call this API straight
    # from the browser via htmx, cross-origin from the frontend on port 3200.
    # Without these headers the browser blocks the response before htmx ever
    # sees it, which is what surfaced as generic "action failed" errors on
    # every direct-call action (nurse acknowledge/complete/cancel included).
    #
    # htmx adds its own headers to every request (HX-Request always, plus
    # HX-Current-URL / HX-Target / HX-Trigger depending on the call) on top of
    # whatever hx-headers sets. A hardcoded allowlist here
    # ("Content-Type, X-User-Role") did not cover those, so the browser's
    # preflight saw an actual request that would carry headers it hadn't been
    # granted and silently blocked it before it ever reached the server -
    # invisible to curl/Postman, which don't enforce CORS at all. Reflecting
    # back whatever the browser's preflight actually asks for (via the
    # Access-Control-Request-Headers it sends) covers every current and
    # future header instead of hardcoding a guess.
    @app.after_request
    def _allow_frontend_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        requested_headers = request.headers.get("Access-Control-Request-Headers")
        response.headers["Access-Control-Allow-Headers"] = requested_headers or "Content-Type, X-User-Role"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response

    @app.route(
        "/<path:_unused>", methods=["OPTIONS"], provide_automatic_options=False
    )
    def _cors_preflight(_unused):
        # htmx sends a preflight OPTIONS request ahead of PUT/DELETE calls with
        # custom headers; answer it directly rather than hitting route logic.
        return "", 204

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
    # Debug mode (Werkzeug's interactive debugger) is opt-in via FLASK_DEBUG
    # so it can never ship "on" by accident to a reachable deployment - set
    # FLASK_DEBUG=true locally if you want auto-reload and tracebacks.
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=5200, debug=debug_mode)
