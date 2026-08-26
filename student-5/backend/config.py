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

    #: Reserved for Release 0 AI-Mode. No LLM call is made while this is false.
    AI_ENABLED = os.environ.get("AI_ENABLED", "false").lower() == "true"
    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3")

    JSON_SORT_KEYS = False
