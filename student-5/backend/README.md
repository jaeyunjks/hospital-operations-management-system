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
| `routes/ai_routes.py` | AI-ready endpoints (structure only) |
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

### AI-ready (structure only)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/shifts/suggest-staff` | Eligible candidate staff. Body: `{"shift_id": 1, "limit": 5}` |
| POST | `/api/shifts/coverage-summary` | Coverage shaped for LLM summarisation |

Both are **manager-only**, like every other workforce-wide read.

**No LLM call is made yet.** Both endpoints return `"ai_enabled": false`,
`"mode": "rule-based"`, a deterministic result, and a `context` block holding
the payload the model will receive. Ollama and the approved open-source model
are connected during the AI integration task (`S5-AI-001`).

`suggest-staff` returns only candidates that passed the deterministic hard
rules; ineligible staff are excluded outright rather than ranked low. When the
model is connected it will re-order and explain that shortlist without changing
who is on it — the LLM never decides eligibility and never assigns anyone. The
`context.candidates` projection deliberately withholds names and free-text
notes: ranking needs role, department and specialisation, not identity.

### Service

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness, including database service reachability |
| GET | `/api` | Machine-readable endpoint index |

## Requirements

Python 3.x and Flask. The database client uses the standard library `urllib`,
so Flask is the only dependency.

```bash
pip install -r requirements.txt
```

## Running locally

Two processes are needed. **Start the database service first.**

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
| `AI_ENABLED` | `false` | Reserved for AI-Mode; no LLM call while false |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Reserved for AI integration |
| `OLLAMA_MODEL` | `llama3` | Reserved for AI integration |

## Error responses

Every failure returns JSON: `{"error": "<code>", "message": "<description>"}`.

| Status | Code | Raised when |
|--------|------|-------------|
| 400 | `validation_error` | Malformed or missing request data |
| 404 | `not_found` | Unknown staff, shift, assignment, or endpoint |
| 405 | `method_not_allowed` | Wrong HTTP method for the path |
| 409 | `conflict` | Duplicate assignment |
| 503 | `database_service_unavailable` | Database microservice unreachable |

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

## Scope

No frontend code, no authentication, no LLM integration, and no direct SQLite
access. The service reads and writes only Staff & Shift Management data via the
database microservice.
