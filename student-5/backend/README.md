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

The optional Release 1 paths are read-only and cross the shared MCP boundary:

```
Student 5 backend -> shared local MCP -> Student 4 Room & Bed API
Student 5 recovery workflow -> shared local MCP
                            -> homs_staffing_recovery_context
                            -> Student 5 recovery-evidence API
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
| `services/mcp_client.py` | Narrow shared-MCP adapters with fixed tool names and arguments |
| `services/recovery_service.py` | Privacy-safe evidence assembler over existing deterministic services |
| `services/recovery_policy_service.py` | Separately labelled advisory RAG policy enrichment |
| `services/recovery_mcp_service.py` | Controlled MCP evidence enrichment and consistency check |
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
| POST | `/api/shifts/<id>/recovery-workflow` | Return a new shared workflow ID with `in_progress` status |
| GET | `/api/recovery-workflows/<workflow_id>` | Poll Planner, Worker, Reviewer and lifecycle state |
| POST | `/api/recovery-workflows/<workflow_id>/review` | Record advisory Human Review without changing the roster |

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
same `recovery_service` directly. It uses the existing Release 1
`StaffShiftRAGClient` to request documented Student 5 policy guidance, then the
fixed `StaffingRecoveryMCPClient` operation to call the registered
`homs_staffing_recovery_context` tool with only `{"shift_id": <id>}`. It sends
one generic `task` plus the enriched evidence to the separately running shared
`POST /workflows` API. Student 5 does not implement or duplicate RAG, MCP
transport, Planner, Worker, Reviewer, audit, or Human Review logic.

The fixed, privacy-safe RAG question is:

> What documented Student 5 rules govern an understaffed shift: per-shift
> coverage, deterministic eligibility blockers, availability and approved
> unavailability, assignment authority, and the Staff Manager human-approval
> decision boundary?

It contains policy concepts only—no shift ID, staff reference, name,
department, notes, or absence reason. The RAG client fixes the shared request to
`POST /query`, `feature: student-5`, and `top_k: 4`; callers cannot redirect the
query to another feature or endpoint.

The existing operational evidence fields remain at the top level for shared
Multi-Agent compatibility. Two additional fields make the trust boundary
explicit:

- `evidence_separation` identifies the authoritative operational fields and
  labels policy as advisory;
- `policy_context` records `retrieval_status`, the fixed `query`, grounded
  `answer`, `confidence`, validated `citations`, safe `reason`, and limitations.

Phase 3E adds a third separate field, `mcp_context`. It records the fixed tool
name, bounded `shift_id` input, validated tool result and source, retrieval
status, safe error code, and a consistency status. The MCP snapshot must exactly
match the freshly built operational evidence before its result is retained. A
mismatch is recorded as `inconsistent` and the tool result is withheld; it can
never replace backend coverage, eligibility, assignments, or unknowns.

Citation objects retain only metadata validated by the R1 adapter: citation
ID, source document, title, section, retrieval score, and source snippet. RAG
text cannot change `backend_eligible`, coverage, assignments, availability,
constraints, or unknowns. In particular, absent fatigue, overtime, rest,
consecutive-shift, staffing-ratio, patient-load, and acuity policies remain
explicitly unknown.

RAG is optional in the existing architecture. When disabled, unavailable,
timed out, invalid, or insufficient, Staffing Recovery continues from
operational evidence and records an explicit `rag_disabled`, `rag_unavailable`,
`rag_timeout`, `rag_invalid_response`, or `insufficient_context` policy status.
It never fabricates substitute guidance.

MCP is likewise optional and degrades explicitly as `mcp_disabled`,
`mcp_unavailable`, `mcp_timeout`, `mcp_invalid_response`, or `mcp_tool_error`.
The workflow still uses authoritative operational evidence and separately
retrieved RAG policy context. The staffing tool performs only a fixed GET to
the manager-only recovery-evidence endpoint; it cannot accept a URL, select a
different tool, or invoke assignment/staff/shift/availability mutations.

The request returns HTTP `202` with the validated shared `in_progress`
response, real `workflow_id`, polling `Location`, and `Retry-After: 1`. Clients
poll `GET /api/recovery-workflows/<workflow_id>` until the workflow reaches
`awaiting_human_review` or `failed`; completed agent stages appear in the same
response contract. It never submits a review or changes a roster. Connection failure returns
`503 multi_agent_unavailable`, timeout returns `504 multi_agent_timeout`, and
an upstream failure or invalid workflow contract returns a safe `502` response.
The shared service is not part of Docker Compose and is not included in the
backend health check.

`GET /api/recovery-workflows/<workflow_id>` and
`POST /api/recovery-workflows/<workflow_id>/review` proxy the remaining shared
lifecycle for Staff Managers. The review body mirrors the shared API exactly:

```json
{"decision": "approve", "comment": "Accepted as an advisory recommendation."}
```

Accepted decisions are `approve`, `correct`, `reject`, and
`partially_accept`. A non-blank `comment` is required for `correct`; it is
optional for the other decisions. The Student 5 backend returns the shared
workflow envelope (`planner`, `worker`, `reviewer`, `human_review`, and
`final_result`) after validating it. A completed Human Review records whether
the manager accepted the recommendation, but it never calls an assignment
route or writes staff, shift, assignment, availability, or unavailability
data. The shared final result must continue to report
`mutation_executed: false`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `MULTI_AGENT_BASE_URL` | `http://127.0.0.1:8200` | Local shared Multi-Agent API base URL |
| `MULTI_AGENT_TIMEOUT` | `600` | Deadline for each short shared API request; model stages run asynchronously |
| `RAG_ENABLED` | `false` | Enables shared policy enrichment; failure degrades explicitly |
| `RAG_SERVER_URL` | `http://127.0.0.1:8100` | Local shared Release 1 RAG API |
| `RAG_TIMEOUT` | `100` | Bounded policy-query deadline in seconds |
| `MCP_ENABLED` | `false` | Enables controlled workflow MCP enrichment; failure degrades explicitly |
| `MCP_SERVER_URL` | `http://127.0.0.1:8000/mcp` | Shared local MCP Streamable HTTP endpoint |
| `MCP_TIMEOUT` | `15` | Bounded MCP operation deadline in seconds |

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
The recovery workflow separately calls only `homs_staffing_recovery_context`
with its server-selected tool name and one positive `shift_id`. Neither adapter
allows a route or user to supply a tool name or upstream URL. MCP availability
is intentionally excluded from `/health`.

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
separate shared Multi-Agent Server. Enable and start RAG and MCP to add their
separately labelled contexts; either optional service records an explicit
degraded status when disabled or unavailable.

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

Terminal 4 — optional shared RAG policy service:

```bash
python3 ai-services/rag-server/ingest.py
python3 ai-services/rag-server/server.py
```

Terminal 5 — optional shared MCP service, configured to call this backend:

```bash
HOMS_STUDENT5_API_URL=http://127.0.0.1:5500/api \
python3 ai-services/mcp-server/server.py
```

Enable both enrichments when starting the backend:

```bash
RAG_ENABLED=true MCP_ENABLED=true python3 app.py
```

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
| `RAG_ENABLED` | `false` | Enables shared policy enrichment and `/api/rag/ask` |
| `RAG_SERVER_URL` | `http://127.0.0.1:8100` | Shared local RAG API |
| `RAG_TIMEOUT` | `100` | RAG request deadline in seconds |
| `MULTI_AGENT_BASE_URL` | `http://127.0.0.1:8200` | Shared local Multi-Agent API |
| `MULTI_AGENT_TIMEOUT` | `600` | Bounded end-to-end workflow deadline in seconds |

## Error responses

Every failure returns JSON: `{"error": "<code>", "message": "<description>"}`.

| Status | Code | Raised when |
|--------|------|-------------|
| 400 | `validation_error` | Malformed or missing request data |
| 404 | `not_found` | Unknown staff, shift, assignment, or endpoint |
| 405 | `method_not_allowed` | Wrong HTTP method for the path |
| 409 | `conflict` | Duplicate assignment or workflow not awaiting Human Review |
| 503 | `database_service_unavailable` | Database microservice unreachable |
| 502 | `multi_agent_upstream_error` | Shared server rejected the request |
| 502 | `multi_agent_invalid_response` | Shared server response failed validation |
| 503 | `multi_agent_unavailable` | Shared server cannot be reached |
| 504 | `multi_agent_timeout` | A shared workflow API request exceeded its deadline |

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

Use the returned UUID to inspect and review the advisory workflow:

```bash
curl --fail-with-body \
  -H 'X-HOMS-Role: Staff Manager' \
  http://127.0.0.1:5500/api/recovery-workflows/<workflow_id>
```

```bash
curl --fail-with-body -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-HOMS-Role: Staff Manager' \
  -d '{"decision":"reject","comment":"Operational risk remains unresolved."}' \
  http://127.0.0.1:5500/api/recovery-workflows/<workflow_id>/review
```

## Scope

No frontend workflow UI, assignment execution, in-service Planner/Worker/
Reviewer implementation, or direct SQLite access. The advisory workflow
integration calls the separate shared service; operational Staff & Shift data
still crosses only the database microservice boundary.
