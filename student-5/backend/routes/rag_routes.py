"""Manager-only documentation guidance through the shared HOMS RAG server."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from authorization import require_manager
from errors import (
    RAGBadGatewayError,
    RAGTimeoutError,
    RAGUnavailableError,
    ValidationError,
)
from services.rag_client import (
    MAX_QUESTION_LENGTH,
    RAGDisabledError,
    RAGInputError,
    RAGInvalidResponse,
    RAGTimeout,
    RAGUnavailable,
    StaffShiftRAGClient,
)
from validation import require_fields, require_json

rag_blueprint = Blueprint("rag", __name__, url_prefix="/api/rag")


def _question_from(payload):
    unsupported = sorted(set(payload) - {"question"})
    if unsupported:
        raise ValidationError("Unsupported request field.",
                              {"fields": unsupported})
    require_fields(payload, ("question",))
    question = payload["question"]
    if not isinstance(question, str) or not question.strip():
        raise ValidationError("'question' must be a non-blank string.",
                              {"field": "question"})
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValidationError(
            f"'question' must be at most {MAX_QUESTION_LENGTH} characters.",
            {"field": "question", "max_length": MAX_QUESTION_LENGTH},
        )
    return question.strip()


@rag_blueprint.post("/ask")
def ask():
    """Return grounded Staff & Shift documentation guidance."""
    require_manager()
    payload = require_json(request.get_json(silent=True))
    question = _question_from(payload)

    client = StaffShiftRAGClient(
        enabled=current_app.config["RAG_ENABLED"],
        server_url=current_app.config["RAG_SERVER_URL"],
        timeout=current_app.config["RAG_TIMEOUT"],
    )
    try:
        return jsonify(client.ask(question))
    except RAGInputError as error:
        raise ValidationError("'question' is invalid.",
                              {"field": "question"}) from error
    except RAGDisabledError as error:
        raise RAGUnavailableError(
            "Staff & Shift guidance via RAG is not enabled."
        ) from error
    except RAGUnavailable as error:
        raise RAGUnavailableError(
            "Staff & Shift guidance is temporarily unavailable."
        ) from error
    except RAGTimeout as error:
        raise RAGTimeoutError(
            "Staff & Shift guidance request timed out."
        ) from error
    except RAGInvalidResponse as error:
        raise RAGBadGatewayError(
            "Staff & Shift guidance returned an invalid response."
        ) from error
