"""
settings.py - Release 1 settings for the MCP and RAG integrations.

Every setting is a small function that reads the environment each time it is
called, so tests can switch values (e.g. monkeypatch MCP_ENABLED) mid-run.
Nothing is read at import time.

The names and defaults match Student 3 (services/mcp_client.py) and Student 5
(config.py) so all backends are configured the same way in docker-compose.
"""

import math
import os

# ------------------------------------------------------------
# Defaults - identical to the values Student 3 and Student 5 use.
# ------------------------------------------------------------
DEFAULT_MCP_SERVER_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_MCP_TIMEOUT = 15.0
DEFAULT_RAG_SERVER_URL = "http://127.0.0.1:8100"
DEFAULT_RAG_TIMEOUT = 100.0

# Values (compared in lower case) that switch a feature on.
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_flag(name):
    """True only for 1/true/yes/on (any case); missing, empty or other = off."""
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def _env_url(name, default):
    """Read a URL, trim spaces and trailing slashes; blank falls back to default."""
    value = os.environ.get(name, "").strip().rstrip("/")
    return value or default


def _env_timeout(name, default):
    """Read a timeout in seconds; anything not a usable number gives the default."""
    try:
        value = float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    # Reject nan/inf and zero/negative values - none is a usable deadline.
    if not math.isfinite(value) or value <= 0:
        return default
    return value


# ------------------------------------------------------------
# MCP (shared MCP server)
# ------------------------------------------------------------
def MCP_ENABLED():
    """Whether calls to the MCP server are allowed (default: off)."""
    return _env_flag("MCP_ENABLED")


def MCP_SERVER_URL():
    """MCP endpoint URL, without a trailing slash."""
    return _env_url("MCP_SERVER_URL", DEFAULT_MCP_SERVER_URL)


def MCP_TIMEOUT():
    """Per-operation MCP deadline in seconds."""
    return _env_timeout("MCP_TIMEOUT", DEFAULT_MCP_TIMEOUT)


# ------------------------------------------------------------
# RAG (shared RAG server)
# ------------------------------------------------------------
def RAG_ENABLED():
    """Whether calls to the RAG server are allowed (default: off)."""
    return _env_flag("RAG_ENABLED")


def RAG_SERVER_URL():
    """RAG server base URL, without a trailing slash."""
    return _env_url("RAG_SERVER_URL", DEFAULT_RAG_SERVER_URL)


def RAG_TIMEOUT():
    """Per-request RAG deadline in seconds."""
    return _env_timeout("RAG_TIMEOUT", DEFAULT_RAG_TIMEOUT)
