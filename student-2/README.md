# student-2

Independently owned feature microservice set for the Hospital Operations
Management System.

- Feature area: Clinical Staff Management (Doctor / Nurse / Specialist workflows)
- Owner: see `docs/architecture/feature-ownership.md`

## Layout

| Path        | Purpose                                                    |
|-------------|------------------------------------------------------------|
| `frontend/` | Flask + Jinja frontend for this feature (port 3200)         |
| `backend/`  | Flask REST API - CRUD + Agentic AI integration (port 5200) |
| `database/` | SQLite database service, schema + seed data (port 6200)    |
| `tests/`    | pytest suite for this microservice                          |

Each of `frontend/`, `backend/`, and `database/` has its own `Dockerfile`,
`requirements.txt`, and (for the database) `schema.sql` / `seed_data.sql`.

## Status

Implemented: clinical record CRUD (with admission-scoped reads and the
post-discharge audit flag), consultation requests, care tasks, surgery
request scheduling with Room & Bed dispatch, and AI-generated admission
summaries with human review. Release 1 adds frontend-to-backend access to the
shared `homs_open_care_tasks` MCP tool and feature-scoped RAG guidance with
citations, confidence and explicit insufficient-context handling. All five
database tables are seeded with realistic sample data. The pytest suite
(`tests/`) covers the role, admission-validation, soft-delete, MCP and RAG
rules.

Authentication is a temporary stand-in (`backend/auth.py`) until the team's
shared authentication service exists; see that file's docstring for how to
switch the active test user.

Cross-service calls to Patient & Admission, Staff & Shift, and Room & Bed
(`backend/services/external_services.py`) are stubbed until those services
exist, with the real HTTP call left commented directly above each stub.

## Authoritative integrated deployment

Use the repository-root [`docker-compose.yml`](../docker-compose.yml) for the
integrated HOMS application. It contains the shared homepage plus the five
frontend/backend/database stacks: 16 application services. Ollama, MCP, RAG
and the shared agentic loop run on the host and are not Compose services.

The older [`student-2/docker-compose.yml`](docker-compose.yml) is a
legacy/standalone development file. Its `http://ollama:11434` default reflects
the older containerised-Ollama topology and must not be used as guidance for
the integrated Release 1 deployment. Root Compose correctly configures the
Student 2 backend to reach host Ollama at
`http://host.docker.internal:11434`.

## MCP and RAG modes

### CI/default mode

Root Compose maps the exact feature-specific variables below into the generic
`MCP_ENABLED` and `RAG_ENABLED` settings consumed by the Student 2 backend:

- `student2_MCP_ENABLED` — defaults to `false`
- `student2_RAG_ENABLED` — defaults to `false`

This lets the normal stack and CI start without local MCP or RAG processes.
The Student 2 workflow also sets both the generic backend flags and these
root-Compose variables to `false`.

### Local Release 1 demo mode

Start host services in this order from the repository root:

1. `ollama serve`
2. Start MCP in Docker-access mode with `HOMS_MCP_HOST=0.0.0.0` and
   `HOMS_MCP_ALLOW_DOCKER_HOST=true`.
3. Run `python3 ai-services/rag-server/ingest.py`.
4. Start RAG in Docker-access mode with `HOMS_RAG_HOST=0.0.0.0` and
   `HOMS_RAG_ALLOW_DOCKER_HOST=true`.
5. Start Student 2 with its exact root-Compose enable variables:

   ```bash
   student2_MCP_ENABLED=true \
   student2_RAG_ENABLED=true \
   docker compose up -d student-2-backend student-2-frontend
   ```

The backend container reaches MCP and RAG through
`host.docker.internal:8000/mcp` and `host.docker.internal:8100`. The Student 2
frontend continues to call only the Student 2 backend; it never calls either
shared service directly. See the root README for the complete five-feature
demo startup sequence.
