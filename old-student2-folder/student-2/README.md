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
summaries with human review. All five database tables are seeded with
realistic sample data. The pytest suite (`tests/`) covers the role,
admission-validation, and soft-delete rules for each route file.

Authentication is a temporary stand-in (`backend/auth.py`) until the team's
shared authentication service exists; see that file's docstring for how to
switch the active test user.

Cross-service calls to Patient & Admission, Staff & Shift, and Room & Bed
(`backend/services/external_services.py`) are stubbed until those services
exist, with the real HTTP call left commented directly above each stub.
