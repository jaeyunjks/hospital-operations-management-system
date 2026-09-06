# student-3

Independently owned feature microservice set for the Hospital Operations
Management System. **Scaffold only — no feature logic implemented yet.**

> Prerequisite: install Ollama and run `ollama pull llama3.2:3b`; without the model, AI advisories use intentional `SOURCE: FALLBACK` rule-based suggestions while the application continues to work.

- Owner: _TBD_ (see `docs/architecture/feature-ownership.md`)
- Feature area: _TBD_

## Layout

| Path        | Purpose                                                    |
|-------------|------------------------------------------------------------|
| `frontend/` | HTMX + HTML/CSS/JS frontend for this feature               |
| `backend/`  | Flask REST API (CRUD + Agentic AI integration)             |
| `database/` | SQLite database, seed/migration scripts                    |
| `tests/`    | Tests for this microservice                                |
| `Dockerfile`| Container build for this microservice (placeholder)        |

## Not yet implemented

CRUD endpoints, database schema, authentication, and AI workflows are
intentionally out of scope for the current repository-initialisation task.
