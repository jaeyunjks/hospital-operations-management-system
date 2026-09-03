"""Shared response envelope, matching the team-wide convention."""

from flask import jsonify


def ok(data, status=200):
    return jsonify({"success": True, "data": data, "error": None}), status


class ApiError(Exception):
    """Raise anywhere in a route to return a clean error response."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status
