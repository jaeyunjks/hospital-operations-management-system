"""
Backend/API microservice — Room & Bed Management (Student 4).

Port 5400. Owns the workflow rules for rooms, beds, operating theatres,
arrangements and shortage cases. Reads and writes data only through the
database microservice on port 6400, and calls Ollama for advisory
suggestions.
"""

import requests
from flask import Flask, jsonify

import config
from responses import ok, ApiError
from routes.ai import bp as ai_bp
from routes.arrangements import bp as arrangements_bp
from routes.catalogue import bp as catalogue_bp
from routes.shortages import bp as shortages_bp
from routes.theatres import bp as theatres_bp
from services import database_client as dbc
from services import ollama_client


def create_app():
    app = Flask(__name__)

    for blueprint in (catalogue_bp, arrangements_bp, theatres_bp, shortages_bp, ai_bp):
        app.register_blueprint(blueprint, url_prefix="/api")

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        return jsonify({"success": False, "data": None, "error": error.message}), error.status

    @app.errorhandler(404)
    def handle_not_found(_error):
        return jsonify({"success": False, "data": None, "error": "Endpoint not found"}), 404

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        app.logger.exception("Unhandled error")
        return jsonify({
            "success": False, "data": None,
            "error": "Unexpected server error: {}".format(error),
        }), 500

    @app.get("/health")
    def health():
        """Reports its own state plus both dependencies.

        Used as the Docker Compose health check and as evidence that the
        service degrades rather than fails when the AI is absent.
        """
        try:
            database = dbc.health()
            database_ok = True
        except ApiError as error:
            database, database_ok = {"error": error.message}, False

        return ok({
            "service": "student-4-backend",
            "port": config.PORT,
            "database": {"ok": database_ok, "detail": database},
            "ai": {
                "ok": ollama_client.is_available(),
                "model": config.OLLAMA_MODEL,
                "note": "AI is advisory; the feature works without it",
            },
        }, status=200 if database_ok else 503)

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
