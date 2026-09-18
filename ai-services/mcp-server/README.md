# HOMS Shared MCP Server

Minimal Release 1 Model Context Protocol server shared by the five HOMS
feature areas. It exposes `homs_echo` for connectivity and
`homs_ward_occupancy_status` for read-only controlled access to Student 4's
published backend API. It never accesses student databases, Ollama, or RAG.

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
| `HOMS_STUDENT4_API_URL` | `http://127.0.0.1:5400/api` | Student 4 backend API base URL, including `/api`; not the database service |
| `HOMS_STUDENT4_API_TIMEOUT` | `10` | Overall upstream deadline and per-stage HTTP timeout in seconds; range 0.1–30 |

The upstream URL must use HTTP(S), with no credentials, query or fragment.
Its destination is operator-configured, never supplied as a tool argument.
Redirects and environment proxies are disabled. The async HTTP client is a
direct pinned dependency (`httpx==0.28.1`); `mcp==2.2.0` is unchanged.

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
plus deterministic output. Ward-tool tests mock the Student 4 HTTP boundary,
including timeout/unavailability, malformed aggregates, exact filtering,
unexpected arguments and privacy projection. No Student 4 service is needed.

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

## Ward occupancy tool

`homs_ward_occupancy_status` accepts only the optional `ward` argument:

```json
{}
```

```json
{"ward": "Emergency"}
```

Omitted/null returns all wards. A supplied ward must be a non-blank string of
at most 200 characters and match Student 4 exactly, including case and spacing.
The tool does not trim, fuzzy-match or translate Student 5 departments (for
example, `Intensive Care` is not mapped to `Critical Care`). Unexpected fields
are rejected before HTTP access.

The only upstream operation is `GET /api/wards/occupancy`, optionally with
`?ward=...`. Student 4 remains authoritative; MCP does not import its services
or access port 6400/SQLite. Student 5 integration is not implemented.

Success uses the same `schema_version`, `ok`, `tool`, `data`, `error` envelope:

```json
{
  "schema_version": "1.0",
  "ok": true,
  "tool": "homs_ward_occupancy_status",
  "data": {
    "requested_ward": "Emergency",
    "wards": [{
      "ward": "Emergency",
      "total_beds": 3,
      "occupied": 1,
      "available": 2,
      "reserved": 0,
      "maintenance": 0,
      "monitored_beds": 3,
      "occupancy_pct": 33.3,
      "care_categories": ["Short-term"]
    }],
    "totals": {
      "total_beds": 3,
      "occupied": 1,
      "available": 2,
      "reserved": 0,
      "maintenance": 0,
      "occupancy_pct": 33.3
    },
    "source": "student-4-room-bed-api"
  },
  "error": null
}
```

These numbers are illustrative, not evidence of live occupancy. The tool
validates required types, count/percentage consistency and aggregate totals;
then constructs an allowlisted result. Extra upstream fields are discarded.
No patient/admission/arrangement/room/bed IDs, names, procedures, sessions or
free-text notes are exposed. This is the current recorded snapshot only—not
future occupancy, staffing requirements, nurse ratios, clinical safety or a
risk score. `monitored_beds` counts monitoring-capable slots, not patient acuity.

All failures set MCP `isError` and return `data: null` plus a structured error:

| Code | Meaning |
|---|---|
| `validation_error` | Blank/wrong-type/oversized ward or unexpected arguments |
| `WARD_NOT_FOUND` | Valid empty response for an exact ward query |
| `upstream_unavailable` | Connection/transport failure or upstream HTTP 5xx (except 504) |
| `upstream_timeout` | Overall deadline, HTTP client timeout, upstream 408/504 |
| `upstream_invalid_response` | Other non-200 response (including redirects), invalid JSON/envelope, malformed/inconsistent aggregates, or wrong ward filter |

Raw upstream errors, URLs and response bodies are not copied into error results.
An empty all-wards result is valid, while an empty exact-ward result is not found.
The current Student 4 occupancy route is read-only and has no authentication
guard; this adapter does not claim to add authentication. Future read-API
authentication needs an explicit service credential contract.
