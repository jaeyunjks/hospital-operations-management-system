"""Neutral system tools for validating the shared MCP contract."""

from __future__ import annotations

from typing import Any, Dict


SCHEMA_VERSION = "1.0"
TOOL_NAME = "homs_echo"
MAX_MESSAGE_LENGTH = 200
_MISSING = object()


def validation_error(message: str, details: Dict[str, Any]) -> Dict[str, Any]:
    """Return the stable structured envelope for invalid tool input."""

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "tool": TOOL_NAME,
        "data": None,
        "error": {
            "code": "validation_error",
            "message": message,
            "details": details,
        },
    }


def homs_echo(message: object = _MISSING) -> Dict[str, Any]:
    """Echo one bounded string without accessing any external resource."""

    if message is _MISSING:
        return validation_error(
            "'message' is required.",
            {"field": "message", "reason": "required"},
        )

    if not isinstance(message, str):
        return validation_error(
            "'message' must be a string.",
            {
                "field": "message",
                "reason": "wrong_type",
                "received_type": type(message).__name__,
            },
        )

    if not message.strip():
        return validation_error(
            "'message' must not be blank.",
            {"field": "message", "reason": "blank"},
        )

    if len(message) > MAX_MESSAGE_LENGTH:
        return validation_error(
            f"'message' must be at most {MAX_MESSAGE_LENGTH} characters.",
            {
                "field": "message",
                "reason": "too_long",
                "max_length": MAX_MESSAGE_LENGTH,
                "received_length": len(message),
            },
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "tool": TOOL_NAME,
        "data": {"message": message},
        "error": None,
    }
