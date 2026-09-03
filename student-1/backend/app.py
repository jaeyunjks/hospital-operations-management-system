# Backend API / Microservice for the Patient & Admissions system.
# Creation date: 31/08/2026

# Port 5100

import os
import sys

from flask import Flask, jsonify

if __package__ in (None, ""):
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

try:
    import backend.config as config
    from backend.auth import AuthError
    from backend.responses import ok, ApiError
    from backend.routes.ai_endpoints import bp as ai_bp
    from backend.routes.patients import patients_bp
    from backend.routes.admissions import admissions_bp
except ImportError:  # pragma: no cover - supports local execution
    import config
    from auth import AuthError
    from responses import ok, ApiError
    from routes.ai_endpoints import bp as ai_bp
    from routes.patients import patients_bp
    from routes.admissions import admissions_bp

SERVICE_NAME = "student-1-backend"
FEATURE = "Patient & Admissions Management"

def create_app():
    app = Flask(__name__)

    app.register_blueprint(ai_bp)
    app.register_blueprint(patients_bp)
    app.register_blueprint(admissions_bp)

    @app.route('/health', methods=['GET'])
    @app.route('/api/health', methods=['GET'])
    def health():
        return ok({'status': 'ok', 'service': 'student-1-backend'})

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        return ok({'error': error.message}, error.status)

    @app.errorhandler(AuthError)
    def handle_auth_error(error):
        return ok({'error': error.message}, error.status_code)

    @app.errorhandler(404)
    def handle_not_found(_error):
        return ok({'error': 'Resource not found'}, 404)

    @app.get("/api")
    def api_index():
        return ok({
            "service": SERVICE_NAME,
            "feature": FEATURE,
            "endpoints": {
                "health": {
                    "method": "GET",
                    "path": "/api/health"
                },
                "ai": {
                    "summary": {
                        "method": "POST",
                        "path": "/api/ai/summary"
                    }
                },
                "patients": {
                    "list": {
                        "method": "GET",
                        "path": "/api/patients"
                    },
                    "create": {
                        "method": "POST",
                        "path": "/api/patients"
                    },
                    "get": {
                        "method": "GET",
                        "path": "/api/patients/<int:patient_id>"
                    },
                    "update": {
                        "methods": ["PUT", "PATCH"],
                        "path": "/api/patients/<int:patient_id>"
                    },
                    "delete": {
                        "method": "DELETE",
                        "path": "/api/patients/<int:patient_id>"
                    },
                    "restore": {
                        "methods": ["POST", "PUT", "PATCH"],
                        "path": "/api/patients/<int:patient_id>/restore"
                    }
                },
                "admissions": {
                    "list": {
                        "method": "GET",
                        "path": "/api/admissions"
                    },
                    "create": {
                        "method": "POST",
                        "path": "/api/admissions"
                    },
                    "get": {
                        "method": "GET",
                        "path": "/api/admissions/<int:admission_id>"
                    },
                    "update": {
                        "methods": ["PUT", "PATCH"],
                        "path": "/api/admissions/<int:admission_id>"
                    }
                }
            }
        })

    return app

app = create_app()


if __name__ == '__main__':
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
