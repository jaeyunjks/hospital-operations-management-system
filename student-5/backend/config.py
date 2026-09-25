"""Configuration for the Student 5 backend/API microservice."""

from __future__ import annotations

import os


class Config:
    """Runtime configuration, overridable by environment variable."""

    #: Base URL of the database microservice. The backend reaches its data
    #: only through this address — it never opens the SQLite file itself.
    DATABASE_SERVICE_URL = os.environ.get(
        "DATABASE_SERVICE_URL", "http://127.0.0.1:6500"
    )

    #: Seconds to wait on a database service call before giving up.
    DATABASE_SERVICE_TIMEOUT = float(os.environ.get("DATABASE_SERVICE_TIMEOUT", "5"))

    #: Port this backend listens on.
    PORT = int(os.environ.get("BACKEND_PORT", "5500"))

    #: AI-Mode master switch. While false no LLM call is attempted at all and
    #: every AI-ready endpoint serves its deterministic result.
    AI_ENABLED = os.environ.get("AI_ENABLED", "false").lower() == "true"
    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")

    #: Seconds to wait on Ollama before abandoning the call and serving the
    #: deterministic ordering instead. Deliberately short: ranking is an
    #: enhancement to a list the manager already has, so a slow model must
    #: never hold up the roster. A miss costs the rationales, nothing more.
    OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "8"))

    #: Optional Release 1 MCP integration. It is independent of AI Mode and
    #: deliberately disabled by default so every Release 0 path works without
    #: the local, non-containerised shared MCP server.
    MCP_ENABLED = os.environ.get("MCP_ENABLED", "false").lower() == "true"
    MCP_SERVER_URL = os.environ.get(
        "MCP_SERVER_URL", "http://127.0.0.1:8000/mcp"
    )
    MCP_TIMEOUT = float(os.environ.get("MCP_TIMEOUT", "15"))

    #: Optional Release 1 RAG integration. Student 5 uses it only for
    #: documentation guidance; live roster data remains behind Student 5's
    #: existing application services and database boundary.
    RAG_ENABLED = os.environ.get("RAG_ENABLED", "false").lower() == "true"
    RAG_SERVER_URL = os.environ.get(
        "RAG_SERVER_URL", "http://127.0.0.1:8100"
    )

    #: The shared RAG server allows up to 90 seconds for its local model call.
    #: Keep the caller deadline just above that bounded operation.
    RAG_TIMEOUT = float(os.environ.get("RAG_TIMEOUT", "100"))

    JSON_SORT_KEYS = False
