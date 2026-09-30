"""
mcp_client.py - calls tools on the shared HOMS MCP server.

What it does:
  * call_tool(name, arguments) runs ONE tool and returns the tool's `data`
    dict from the server's standard reply envelope
    ({schema_version, ok, tool, data, error}).
  * get_status() reports whether MCP is on and reachable, for the UI / CI.
  * Uses the same `mcp` SDK client as Student 3 (Streamable HTTP), one short
    session per call. No retries and no caching.

ERROR-HANDLING CONTRACT (different from ollama_client.py, on purpose):
  * ollama_client swallows every failure into a fallback string because the AI
    summary is optional. MCP callers need to know WHY a call failed, so
    call_tool raises one of four classified errors instead.
  * Every error message is fixed text: no traceback, server address or SDK
    detail ever appears in one, and the original exception is not chained.
  * The on/off switch, URL and timeout come from settings.py and are read on
    every call.
"""

import asyncio
import math
import re
from urllib.parse import urlsplit

import settings

SCHEMA_VERSION = "1.0"
# Every reply from the shared server carries these five parts.
_ENVELOPE_KEYS = ("schema_version", "ok", "tool", "data", "error")
# Tool error codes are short identifiers like "validation_error".
_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


# ------------------------------------------------------------
# Errors
# ------------------------------------------------------------
class McpError(Exception):
    """Base class, so a caller can catch every MCP failure at once."""


class McpDisabled(McpError):
    """MCP_ENABLED is off."""


class McpUnavailable(McpError):
    """The server is unreachable, timed out, or the configured URL is unusable."""


class McpToolError(McpError):
    """The tool ran and returned an error; `code` is the tool's error code."""

    def __init__(self, code):
        super().__init__(f"MCP tool returned error '{code}'")
        self.code = code


class McpBadReply(McpError):
    """The server answered, but the reply is missing expected parts."""


# ------------------------------------------------------------
# Connection
# ------------------------------------------------------------
def _url_is_usable(url):
    """Only plain http(s) URLs with a host and no embedded credentials."""
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


async def _with_client(url, timeout, operation):
    """Open one short-lived MCP session and run `operation(client)` in it."""
    from mcp import Client  # lazy: the backend still starts without the SDK

    # Whole-call deadline plus the SDK's own read deadline - never hang.
    async with asyncio.timeout(timeout):
        async with Client(url, read_timeout_seconds=timeout) as client:
            return await operation(client)


def _run(operation):
    """Check the switch, run the operation, and map transport failures."""
    # Read settings fresh on every call so tests can flip them mid-run.
    if not settings.MCP_ENABLED():
        raise McpDisabled("MCP integration is disabled.")

    url = settings.MCP_SERVER_URL()
    timeout = settings.MCP_TIMEOUT()
    if not _url_is_usable(url) or not math.isfinite(timeout) or timeout <= 0:
        raise McpUnavailable("MCP server settings are not usable.")

    try:
        return asyncio.run(_with_client(url, timeout, operation))
    except McpError:
        raise
    except Exception:
        # Timeouts, refused connections, DNS errors and the SDK's exception
        # groups all land here. `from None` drops the chained cause so the
        # server address never reaches a traceback or log line.
        raise McpUnavailable("MCP server is unreachable or timed out.") from None


# ------------------------------------------------------------
# Reply checking
# ------------------------------------------------------------
def _read_reply(name, response):
    """Validate one tool reply; return `data` on success or raise."""
    envelope = getattr(response, "structured_content", None)

    # All five envelope parts must be present, and must describe this tool.
    if not isinstance(envelope, dict) or any(k not in envelope for k in _ENVELOPE_KEYS):
        raise McpBadReply("MCP reply is missing expected parts.")
    if envelope["schema_version"] != SCHEMA_VERSION or envelope["tool"] != name:
        raise McpBadReply("MCP reply is missing expected parts.")

    ok = envelope["ok"]
    # `ok` must be a real boolean and agree with the protocol-level error flag.
    if not isinstance(ok, bool) or ok == bool(getattr(response, "is_error", False)):
        raise McpBadReply("MCP reply is missing expected parts.")

    if not ok:
        # The tool reported a failure: pass its error code up, nothing else.
        error = envelope["error"]
        code = error.get("code") if isinstance(error, dict) else None
        if envelope["data"] is not None or not isinstance(code, str) \
                or not _CODE_PATTERN.match(code):
            raise McpBadReply("MCP reply is missing expected parts.")
        raise McpToolError(code)

    if envelope["error"] is not None or not isinstance(envelope["data"], dict):
        raise McpBadReply("MCP reply is missing expected parts.")
    return envelope["data"]


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------
def call_tool(name, arguments=None):
    """
    Call one MCP tool and return its `data` dict.

    Raises:
      McpDisabled    - MCP_ENABLED is off (nothing is contacted).
      McpUnavailable - server unreachable, timed out, or URL/timeout unusable.
      McpToolError   - the tool returned an error; see `.code`.
      McpBadReply    - the reply is missing expected parts.
      ValueError     - `name` is not a non-empty string (a caller bug).
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Tool name must be a non-empty string.")
    arguments = {} if arguments is None else arguments

    async def operation(client):
        return await client.call_tool(name, arguments)

    return _read_reply(name, _run(operation))


def get_status():
    """
    Report MCP state for the UI / CI. Never raises.

    Returns {"enabled": bool, "reachable": bool, "tools": [tool names]} and, when
    not reachable while enabled, a short "error" string. The server is only
    contacted when MCP is enabled.
    """
    status = {"enabled": settings.MCP_ENABLED(), "reachable": False, "tools": []}
    if not status["enabled"]:
        return status

    async def operation(client):
        return await client.list_tools()

    try:
        listing = _run(operation)
        # Each listed tool must carry a name string.
        names = [tool.name for tool in listing.tools]
        if not all(isinstance(n, str) for n in names):
            raise McpBadReply("MCP reply is missing expected parts.")
    except McpError as error:
        # Messages are fixed, safe text - fine to hand to the UI.
        return {**status, "error": str(error)}
    except (AttributeError, TypeError):
        return {**status, "error": "MCP reply is missing expected parts."}

    return {**status, "reachable": True, "tools": names}
