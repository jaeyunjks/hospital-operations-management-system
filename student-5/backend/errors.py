"""Error types and handlers for the Student 5 backend/API microservice.

Every failure leaves the API as a JSON object of the form::

    {"error": "<machine_readable_code>", "message": "<human readable>"}
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from flask import Flask, jsonify


class ApiError(Exception):
    """Base class for errors that map onto an HTTP status code."""

    status_code = 500
    error_code = "internal_error"

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_response(self):
        payload: Dict[str, Any] = {"error": self.error_code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return jsonify(payload), self.status_code


class ValidationError(ApiError):
    """The request body or query string failed validation."""

    status_code = 400
    error_code = "validation_error"


class NotFoundError(ApiError):
    """The requested resource does not exist."""

    status_code = 404
    error_code = "not_found"


class ConflictError(ApiError):
    """The request clashes with current state (e.g. a duplicate assignment)."""

    status_code = 409
    error_code = "conflict"


class DatabaseServiceError(ApiError):
    """The database microservice is unreachable or returned an unusable reply."""

    status_code = 503
    error_code = "database_service_unavailable"


def register_error_handlers(app: Flask) -> None:
    """Attach JSON error handling to the application."""

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return error.to_response()

    @app.errorhandler(404)
    def handle_not_found(error):
        return jsonify({"error": "not_found", "message": "Endpoint not found."}), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(error):
        return jsonify({"error": "method_not_allowed",
                        "message": "Method not allowed for this endpoint."}), 405

    @app.errorhandler(Exception)
    def handle_unexpected(error):
        app.logger.exception("Unhandled error")
        return jsonify({"error": "internal_error",
                        "message": "An unexpected error occurred."}), 500
