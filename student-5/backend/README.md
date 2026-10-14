# Student 5 — Backend/API Microservice

**Feature:** Staff & Shift Management
**Layer:** Application service layer (Flask REST API)

Implements prompt artefact `S5-BE-001`
([`docs/prompts/student-5/backend-development.md`](../../docs/prompts/student-5/backend-development.md)).

## Architecture

```
HTMX Frontend
     |  REST
     v
Backend/API Microservice   <- this service (port 5500)
     |  REST
     v
Database Microservice      <- student-5/database/service.py (port 6500)
     |
     v
SQLite
```

The optional Release 1 path is read-only and crosses the shared MCP boundary:

```
Student 5 backend -> shared local MCP -> Student 4 Room & Bed API
```

It never calls Student 4 directly or reads Student 4 data files. The shared
MCP server runs outside Student 5 containers and is not required for normal
Release 0 operation.

The backend holds the application logic and validation. **It never opens the
SQLite file** — every read and write crosses the HTTP boundary in
`database_client.py`. If the database service is down, the backend returns
`503 database_service_unavailable` rather than falling back to the file.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask application factory, `/health`, `/api` index, blueprint registration |
| `config.py` | Environment-driven configuration |
| `database_client.py` | HTTP client for the database microservice (the API boundary) |
| `validation.py` | Request validation helpers |
| `errors.py` | `ApiError` hierarchy and JSON error handlers |
| `routes/staff_routes.py` | Staff endpoints |
| `routes/shift_routes.py` | Shift endpoints |
| `routes/assignment_routes.py` | Assignment endpoints |
| `routes/coverage_routes.py` | Staffing coverage endpoint |
| `routes/recovery_routes.py` | Read-only Staffing Recovery evidence endpoint |
| `routes/ai_routes.py` | AI-ready endpoints (structure only) |
| `routes/mcp_routes.py` | Manager-only ward occupancy MCP gateway |
| `services/mcp_client.py` | Narrow shared-MCP adapter |
| `services/recovery_service.py` | Privacy-safe evidence assembler over existing deterministic services |
| `services/multi_agent_client.py` | Bounded HTTP adapter for the shared Multi-Agent lifecycle |
| `services/*.py` | Application logic, separated from routing and storage |

## Endpoints

### Staff

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/staff` | List staff. Filters: `department`, `role`, `availability_status` |
| GET | `/api/staff/search` | Free-text search. Query: `q`, plus the filters above |
| PUT | `/api/staff/<id>/availability` | Set availability. Body: `{"availability_status": "..."}` |

### Shift

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/shifts` | List shifts. Filters: `department`, `shift_date`, `shift_status` |
| POST | `/api/shifts` | Create a shift |
| GET | `/api/shifts/<id>` | Retrieve one shift |
| PUT | `/api/shifts/<id>` | Update a shift |
| DELETE | `/api/shifts/<id>` | Delete a shift (assignments cascade) |
| GET | `/api/shifts/<id>/recovery-evidence` | Assemble read-only Staffing Recovery evidence (Staff Manager only) |
| POST | `/api/shifts/<id>/recovery-workflow` | Start a shared advisory workflow and stop at Human Review |

### Assignment

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/shifts/<id>/assignments` | Staff assigned to a shift |
| GET | `/api/shifts/<id>/candidates` | Staff evaluated against this shift, eligible or not |
| POST | `/api/shifts/<id>/assign` | Assign staff. Body: `{"staff_id": 1, "approved_by": "..."}` |
| PUT | `/api/shifts/<id>/unassign` | Withdraw staff. Body: `{"staff_id": 1}` |

`unassign` sets the assignment to `Cancelled` rather than deleting the row, so
the roster history is retained. That is why it is a `PUT` and not a `DELETE`.
Re-assigning a cancelled staff member reinstates the existing record, which
respects the schema's `UNIQUE (shift_id, staff_id)` constraint.

### Candidate eligibility

`services/eligibility_service.py` is the single source of truth for who may be
assigned to a shift. Both the frontend's candidate list and `suggest-staff`
call it, so the two can never disagree.

Eligibility is a yes/no decision, never a score. **Hard rules** block: already
assigned to this shift, an operational status of `Unavailable` or `On Leave`,
an `Approved` unavailability request covering the shift date, a `required_role`
mismatch, and an overlapping active assignment. Recurring weekly availability
is **advisory** — reported as `weekly_ok` and a note, never blocking, because
the assign endpoint itself permits rostering outside the pattern. Department
and specialisation are **context only**: department orders the list so local
staff surface first, but it can never make someone unassignable.

`/candidates` returns everyone holding the required role, blocked candidates
included, each with its `blocked_reason` — a manager needs to see why someone
cannot be used. `suggest-staff` returns the eligible only.

### Coverage

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/shifts/coverage` | Required vs assigned staffing, per shift and overall |

`coverage_service` is the single source of truth for coverage arithmetic. Each
shift carries `required_staff_count`, `assigned_staff_count`, `filled_staff_count`
(`min(assigned, required)`), `shortfall` (`max(required - assigned, 0)`),
`surplus` (`max(assigned - required, 0)`) and `coverage_status`. The summary
sums those per-shift figures: `total_shifts`, `fully_staffed`, `understaffed`,
`unstaffed`, `overstaffed`, `total_shortfall`, `total_surplus`,
`required_positions`, `assigned_positions`, `filled_positions` and
`coverage_pct`.

Gap and surplus are floored **per shift** before being summed, so extra staff
on one shift can never cancel a shortage on another — A(required 2, assigned 3)
plus B(required 2, assigned 1) reports a gap of 1, not none. Coverage counts
filled positions, so the percentage cannot exceed 100%; surplus is reported
separately rather than folded in. `coverage_pct` is `null` when nothing is
required, because a day with no shifts has no coverage to report.

`understaffed` counts every shift carrying a gap, including the `unstaffed`
ones counted separately. `overstaffed` counts SHIFTS; `total_surplus` counts
POSITIONS.

The frontend consumes these fields rather than recomputing them, so a KPI tile,
a planner roll-up and the API cannot disagree about the same roster.

### Staffing Recovery evidence

`GET /api/shifts/<id>/recovery-evidence` is the Student 5 integration boundary
for a later shared Multi-Agent workflow. It is manager-only and read-only:

```
selected shift
      -> existing shift, coverage, assignment and eligibility services
      -> privacy-safe generic evidence JSON
```

The response contains `schema_version`, `evidence_type`, `evidence_mode`,
`current_state`, `objective`, `options`, `constraints`, `unknowns`, and
`provenance`. The objective uses the existing per-shift coverage arithmetic.
Candidate options project the existing deterministic eligibility result; this
endpoint does not reimplement its hard rules or use Suggest Staff/LLM output.

Staff identifiers are emitted as `staff-<id>`. Names, staff and shift notes,
and absence reasons are excluded. Approved unavailability is represented only
by its dates and status. `provenance.mutation_allowed` is always `false`, and
the endpoint neither calls MCP/RAG/Multi-Agent/Ollama services nor changes an
assignment. It returns the existing JSON 404, 403, 503, or 500 error contracts
instead of constructing evidence when an authoritative source is unavailable.

### Shared Multi-Agent workflow integration

`POST /api/shifts/<id>/recovery-workflow` builds fresh evidence by calling the
same `recovery_service` directly, then sends one generic `task` plus that
evidence to the separately running shared `POST /workflows` API. Student 5 does
not implement or duplicate Planner, Worker, Reviewer, audit, or Human Review
logic.

The request returns the validated shared response, including its real
`workflow_id`, only when the shared status is `awaiting_human_review`. It never
submits a review or changes a roster. Connection failure returns
`503 multi_agent_unavailable`, timeout returns `504 multi_agent_timeout`, and
an upstream failure or invalid workflow contract returns a safe `502` response.
The shared service is not part of Docker Compose and is not included in the
backend health check.

| Variable | Default | Purpose |
|----------|---------|---------|
| `MULTI_AGENT_BASE_URL` | `http://127.0.0.1:8200` | Local shared Multi-Agent API base URL |
| `MULTI_AGENT_TIMEOUT` | `600` | Overall deadline for all three sequential model stages |

### AI-ready (structure only)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/shifts/suggest-staff` | Eligible candidate staff. Body: `{"shift_id": 1, "limit": 5}` |
| POST | `/api/shifts/coverage-summary` | Coverage position, optionally narrated. Body: `{"shift_date": "...", "department": "...", "narrate": false}` |

Both are **manager-only**, like every other workforce-wide read.

### Coverage Summary narration

    coverage_service -> facts -> explicit request -> Ollama -> manager reads

`narrate` is **opt-in and defaults to false**. The deterministic summary is
fetched on every Workforce Overview page view; making the model call implicit
would put an LLM round trip in front of a landing page nobody asked to wait
for. Only the explicit "Generate AI summary" action sets it.

`headline`, `summary` and `gaps` are the authoritative answer and are returned
in full whatever the model does. `narrative` and `priorities` are commentary
laid beside them — never a substitute for a number.

**The model may not produce a figure, only borrow one.** Every integer in the
narrative and its priorities, written as digits or as words, is checked against
the numbers appearing in the facts it was given. A narrative asserting a
staffing figure the roster does not support is discarded and the deterministic
summary served instead, with `fallback_reason: "unsupported_numbers"`. This
fails **closed**: an ordinary turn of phrase can occasionally cost a paragraph,
which is the cheaper error than a fluent wrong number a manager would act on.

Provenance mirrors Suggest Staff: `mode` is `"ai"` only when a narrative
survived validation, and `generation` carries `source`, `model` and
`fallback_reason`. Reasons: `not_requested`, `ai_disabled`, `no_shifts` (no
call made), `model_unavailable`, `invalid_model_output`, `unsupported_numbers`.
Every one returns HTTP 200 with the full deterministic figures.

Data sent to the model is aggregate only — department, date, times, required
role and the position counts. No `shift_id`, no `staff_id`, no names, and no
free text of any kind: shift notes, staff notes and absence reasons never enter
the projection. A roster larger than 20 shifts is truncated worst-first, with
`shifts_total` still reporting the true count.

The prompt lives in `prompts/coverage_summary.py`.

### Suggest Staff ranking

    eligibility_service -> eligible only -> optional Ollama -> manager assigns

`suggest-staff` returns only candidates that passed the deterministic hard
rules; ineligible staff are excluded outright rather than ranked low. When
`AI_ENABLED` is true and the shortlist is non-empty, that shortlist is sent to
Ollama, which re-orders it and adds a short `rationale` per candidate.

The model **cannot change who is on the list**. `ai_service._merge_ranking`
discards any `staff_id` that was not in the shortlist, takes a repeated id
once, and appends anyone the model omitted in the deterministic order they
already had. The model is asked politely in the prompt and prevented in code;
the code is what makes it true. Nothing in this path assigns anyone.

Ranking happens *after* `limit` is applied, so the model reorders the same
people a manager would already have seen rather than choosing who appears.

| Field | Meaning |
|-------|---------|
| `mode` | `"ai"` when the model ranked the list, `"rule-based"` otherwise |
| `ranking.source` | `"ollama"` or `"deterministic"` |
| `ranking.fallback_reason` | `null`, or one of the codes below |
| `note` | Plain-English explanation of what happened to the ranking |

Fallback reasons: `ai_disabled`, `no_candidates` (nothing eligible, so no call
was made), `model_unavailable` (unreachable, refused, or timed out), and
`invalid_model_output` (unusable JSON, or no recognisable `staff_id`). Every
one serves the same deterministic ordering the caller would otherwise have got
— a missing or broken model costs the rationales and the ordering, never the
shortlist, and never an HTTP error. Notes and reason codes never carry an
exception message, host, or stack trace.

**Data sent to the model is minimised.** Candidates are identified by
`staff_id` only — never by name, which the backend re-joins itself after the
model replies. `staff.notes`, shift `notes`, and unavailability request
`reason`/`notes` are excluded outright: free text is where clinical and
personal detail ends up, and none of it bears on which of two eligible nurses
to offer first. Department, specialisation, employment status and the advisory
`weekly_availability_matches` flag are sent, as ranking context only.

The prompt lives in `prompts/suggest_staff.py` rather than inline in the
service, so it is reviewable and citable as an artefact in its own right.

### Service

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness, including database service reachability |
| GET | `/api` | Machine-readable endpoint index |

### Optional shared MCP integration

| Variable | Default | Purpose |
|----------|---------|---------|
| `MCP_ENABLED` | `false` | Enables the independent read-only MCP path |
| `MCP_SERVER_URL` | `http://127.0.0.1:8000/mcp` | Local shared MCP Streamable HTTP endpoint |
| `MCP_TIMEOUT` | `15` | Overall MCP operation deadline in seconds |

`GET /api/mcp/ward-occupancy` calls only
`homs_ward_occupancy_status`. Its optional `ward` query parameter is an exact
Student 4 ward name; Student 5 does not infer department-to-ward mappings.
MCP availability is intentionally excluded from `/health`.

For Docker development, start the shared MCP separately on the host with
`HOMS_MCP_HOST=0.0.0.0` and `HOMS_MCP_ALLOW_DOCKER_HOST=true`, then start
Student 5 with `STUDENT5_MCP_ENABLED=true` and
`STUDENT5_MCP_SERVER_URL=http://host.docker.internal:8000/mcp`. Compose maps
these into the backend container; MCP remains disabled by default and is never
a Compose service.

## Requirements

Python 3.x, Flask and the pinned MCP SDK. The database client uses the standard
library `urllib`.

```bash
pip install -r requirements.txt
```

## Running locally

Two processes are needed for the baseline backend. **Start the database
service first.** The recovery-workflow endpoint additionally requires the
separate shared Multi-Agent Server.

Terminal 1 — database microservice:

```bash
python3 student-5/database/init_db.py
```

```bash
cd student-5/database && python3 service.py
```

Terminal 2 — backend/API microservice:

```bash
cd student-5/backend && python3 app.py
```

Terminal 3 — shared Multi-Agent Server (deterministic local validation):

```bash
python3 ai-services/multi-agent-server/api.py --model-mode deterministic
```

For live Ollama validation, replace `deterministic` with `ollama` and ensure
the configured Planner, Worker, and Reviewer models are available locally.

Confirm both are up:

```bash
curl -s http://127.0.0.1:5500/health
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `BACKEND_PORT` | `5500` | Port this service listens on |
| `DATABASE_SERVICE_URL` | `http://127.0.0.1:6500` | Database microservice base URL |
| `DATABASE_SERVICE_TIMEOUT` | `5` | Seconds before a database call times out |
| `AI_ENABLED` | `false` | AI-Mode switch; no LLM call is attempted while false |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama runtime base URL |
| `OLLAMA_MODEL` | `llama3` | Model used to rank staff and narrate coverage |
| `OLLAMA_TIMEOUT` | `8` | Seconds before abandoning the model and serving deterministic output |
| `MULTI_AGENT_BASE_URL` | `http://127.0.0.1:8200` | Shared local Multi-Agent API |
| `MULTI_AGENT_TIMEOUT` | `600` | Bounded end-to-end workflow deadline in seconds |

## Error responses

Every failure returns JSON: `{"error": "<code>", "message": "<description>"}`.

| Status | Code | Raised when |
|--------|------|-------------|
| 400 | `validation_error` | Malformed or missing request data |
| 404 | `not_found` | Unknown staff, shift, assignment, or endpoint |
| 405 | `method_not_allowed` | Wrong HTTP method for the path |
| 409 | `conflict` | Duplicate assignment |
| 503 | `database_service_unavailable` | Database microservice unreachable |
| 502 | `multi_agent_upstream_error` | Shared server rejected the request |
| 502 | `multi_agent_invalid_response` | Shared server response failed validation |
| 503 | `multi_agent_unavailable` | Shared server cannot be reached |
| 504 | `multi_agent_timeout` | Shared workflow exceeded its deadline |

## Testing

```bash
pytest student-5/tests -v
```

Tests run against an in-memory stub of the database client, so no second
process or SQLite file is required.

## Example requests

```bash
curl -s "http://127.0.0.1:5500/api/staff?department=Emergency"
```

```bash
curl -s -X POST http://127.0.0.1:5500/api/shifts -H 'Content-Type: application/json' -d '{"department":"Emergency","shift_date":"2026-09-01","start_time":"07:00","end_time":"15:00","required_role":"Registered Nurse","required_staff_count":2}'
```

```bash
curl -s -X POST http://127.0.0.1:5500/api/shifts/1/assign -H 'Content-Type: application/json' -d '{"staff_id":1}'
```

```bash
curl -s http://127.0.0.1:5500/api/shifts/coverage
```

```bash
curl --fail-with-body -X POST \
  -H 'X-HOMS-Role: Staff Manager' \
  http://127.0.0.1:5500/api/shifts/11/recovery-workflow
```

## Scope

No frontend workflow UI, Human Review route, assignment execution, in-service
Planner/Worker/Reviewer implementation, or direct SQLite access. The advisory
workflow integration calls the separate shared service; operational Staff &
Shift data still crosses only the database microservice boundary.
