"""Read-only workflow guidance backed by the shared HOMS RAG server."""

from flask import Blueprint, request

try:
    from backend.auth import ROLE_MANAGER, ROLE_RECEPTIONIST, require_role
    from backend.config import RAG_ENABLED, RAG_SERVER_URL, RAG_TIMEOUT
    from backend.responses import ok
    from backend.services.rag_client import (
        RAGDisabled,
        RAGInputError,
        RAGInvalidResponse,
        RAGTimeout,
        RAGUnavailable,
        PatientAdmissionRAGClient,
    )
except ImportError:  # pragma: no cover - supports local execution
    from auth import ROLE_MANAGER, ROLE_RECEPTIONIST, require_role
    from config import RAG_ENABLED, RAG_SERVER_URL, RAG_TIMEOUT
    from responses import ok
    from services.rag_client import (
        RAGDisabled,
        RAGInputError,
        RAGInvalidResponse,
        RAGTimeout,
        RAGUnavailable,
        PatientAdmissionRAGClient,
    )

bp = Blueprint("rag", __name__, url_prefix="/api/rag")


@bp.post("/ask")
def ask():
    require_role(ROLE_MANAGER, ROLE_RECEPTIONIST)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"question"}:
        return ok({"error": "invalid_question"}, 400)

    client = PatientAdmissionRAGClient(
        enabled=RAG_ENABLED,
        server_url=RAG_SERVER_URL,
        timeout=RAG_TIMEOUT,
    )
    try:
        return ok(client.ask(payload["question"]))
    except RAGInputError:
        return ok({"error": "invalid_question"}, 400)
    except RAGDisabled:
        return ok({"error": "rag_disabled"}, 503)
    except RAGTimeout:
        return ok({"error": "rag_timeout"}, 504)
    except RAGUnavailable:
        return ok({"error": "rag_unavailable"}, 503)
    except RAGInvalidResponse:
        return ok({"error": "rag_invalid_response"}, 502)