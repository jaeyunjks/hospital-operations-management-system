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
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")

    #: Seconds to wait on Ollama before abandoning the call and serving the
    #: deterministic ordering instead. Deliberately short: ranking is an
    #: enhancement to a list the manager already has, so a slow model must
    #: never hold up the roster. A miss costs the rationales, nothing more.
    OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "8"))

    JSON_SORT_KEYS = False
