# HOMS Shared MCP Server

Minimal Release 1 Model Context Protocol server shared by the five HOMS
feature areas. It currently exposes one deterministic connectivity tool,
`homs_echo`. It does not access student services, databases, Ollama, or RAG.

## Runtime

- Python 3.12 (matching the repository CI workflows)
- MCP Streamable HTTP transport
- Default endpoint: `http://127.0.0.1:8000/mcp`

The server is deliberately non-containerised. Run it from the repository root.

## Install

Use a virtual environment, then install the runtime dependency and pytest:

```bash
python3 -m pip install -r ai-services/mcp-server/requirements.txt pytest
```

## Start

```bash
python3 ai-services/mcp-server/server.py
```

Configuration is read from the environment:

| Variable | Default | Purpose |
|---|---|---|
| `HOMS_MCP_HOST` | `127.0.0.1` | Network interface on which the local server listens |
| `HOMS_MCP_PORT` | `8000` | Unused repository port reserved for the MCP server |
| `HOMS_MCP_PATH` | `/mcp` | Streamable HTTP endpoint path |

The localhost default also preserves the MCP SDK's DNS-rebinding protection.
Container-to-host routing will be decided when feature backend integration is
implemented; do not expose this scaffold on a wider interface without an
explicit transport-security configuration.

## Test

```bash
python3 -m pytest -q ai-services/mcp-server/tests
```

The tests exercise tool discovery and calls through the SDK's in-memory MCP
transport. They cover valid, missing, blank, wrong-type, and oversized input,
plus deterministic output.

## Tool contract

`homs_echo` accepts exactly one argument:

```json
{"message": "connectivity check"}
```

`message` must be a non-blank string of at most 200 characters. A successful
call returns structured content:

```json
{
  "schema_version": "1.0",
  "ok": true,
  "tool": "homs_echo",
  "data": {"message": "connectivity check"},
  "error": null
}
```

Invalid input returns the same stable envelope with MCP `isError` set and an
error object containing `code`, `message`, and `details`.
