# Hospital Operations Management System

> **Status:** Repository initialisation / scaffolding only. No feature business
> logic, CRUD endpoints, database schemas, authentication, or AI workflows are
> implemented yet.

## Project

**Hospital Operations Management System** — an integrated Agentic AI application
supporting hospital operational coordination.

The system is developed as **five independently owned student feature sets**.
Each feature set contains an **HTMX frontend microservice**, a **Flask
backend/API microservice**, and a **SQLite database microservice**. These are
later integrated into a single team application with a shared UI and shared AI
mode.

## Purpose

Provide an integrated, Agentic-AI-assisted platform that supports the day-to-day
operational coordination of a hospital across admissions, clinical staffing,
medication administration, bed management, and shift management.

## Planned feature areas

| # | Feature area | Owner |
|---|-----------------------------------------|-------------------------|
| 1 | Patient & Admission Management          | _TBD — see `docs/architecture/feature-ownership.md`_ |
| 2 | Doctor / Clinical Staff Management      | _TBD_ |
| 3 | Medication Administration Records       | _TBD_ |
| 4 | Room & Bed Management                   | _TBD_ |
| 5 | Staff / Shift Management                | _TBD_ |

Feature-to-student assignment is tracked in
[`docs/architecture/feature-ownership.md`](docs/architecture/feature-ownership.md).

## Prescribed ASD 2026 technology stack

- **Python** (3.x)
- **Flask** — REST APIs
- **HTMX**
- **HTML5 / CSS3 / JavaScript** — frontend
- **SQLite** — default project database
- **Docker** & **Docker Compose**
- **Git** / **GitHub** / **GitHub Actions** — version control and CI/CD
- **Ollama** — local LLM runtime
- **Approved open-source LLMs** — Llama, Qwen and/or DeepSeek

> The project follows the ASD 2026 prescribed technology stack. Alternative
> frameworks or technologies should only be introduced if permitted by the
> subject requirements and/or tutor.

Cloud deployment will eventually target **Microsoft Azure** (preferred service:
**Azure Container Apps**). Cloud implementation is out of scope for this setup.

## Progressive releases

### Release 0
- Five independently owned student feature sets, each containing an HTMX
  frontend microservice, a Flask backend/API microservice, and a SQLite
  database microservice.
- **AI-Mode** backed by **Ollama** + an approved open-source LLM.
- Agentic loop: **Plan → Act → Observe → Adapt**.
- **Docker Compose** integration of all services.
- Individual **CI/CD** workflow per student.

### Release 1
- **MCP** (Model Context Protocol) services.
- **RAG** (Retrieval-Augmented Generation) services.
- Grounded AI responses backed by MCP + RAG.

### Release 2
- **Multi-Agent System**.
- Testing across the integrated application.
- **Azure** cloud deployment.

> Project-specific implementation details beyond the prescribed ASD release
> requirements will be agreed by the team. See
> [`docs/release-planning.md`](docs/release-planning.md).

## Repository layout

The layout is **aligned with the ASD 2026 prescribed repository structure**.

```
.
├── .github/workflows/     # CI/CD placeholders: student-1..5 + integration + deployment
├── docs/                  # Architecture, reports, and per-release documentation
├── shared/                # Shared frontend (index, CSS/UI theme) and configuration
├── student-1 .. student-5/ # Independently owned feature microservice sets
├── ai-services/           # AI-Mode, MCP, RAG, and Multi-Agent services (later releases)
├── scripts/               # Build / test / deploy helper scripts
└── docker-compose.yml     # Root orchestration scaffold
```

Each `student-N/` contains `frontend/`, `backend/`, `database/`, `tests/`, and a
`Dockerfile`.

## Team members and feature ownership

_Placeholder — to be completed by the team._

| Student | Name | GitHub handle | Feature area |
|---------|------|---------------|--------------|
| student-1 | _TBD_ | _TBD_ | _TBD_ |
| student-2 | _TBD_ | _TBD_ | _TBD_ |
| student-3 | _TBD_ | _TBD_ | _TBD_ |
| student-4 | _TBD_ | _TBD_ | _TBD_ |
| student-5 | _TBD_ | _TBD_ | _TBD_ |

## Local setup

_Placeholder — to be completed as services are implemented._

Prerequisites (planned): Python 3.x, Docker & Docker Compose, Git, and Ollama
with an approved model pulled locally. A typical future workflow will be:

```bash
# Clone
git clone <repo-url>
cd hospital-operations-management-system

# (Later) bring up the integrated stack
docker compose up --build
```

## Architecture

See [`docs/architecture/`](docs/architecture/). High-level: five independent
microservice sets integrated behind a shared frontend and a shared AI mode, with
AI services (Ollama, and later MCP / RAG / Multi-Agent) provided centrally under
[`ai-services/`](ai-services/).

## Development workflow

_Placeholder — to be agreed by the team._ Suggested starting points:

- Each student works within their own `student-N/` directory.
- Shared assets under `shared/` change via team agreement / review.
- Each student maintains their own `.github/workflows/student-N.yml`.

See [`docs/architecture/development-workflow.md`](docs/architecture/development-workflow.md).

## Testing

Each student maintains tests under `student-N/tests/`. Testing and integration
validation are performed throughout the project releases. Release 2 extends the
testing approach with the prescribed pre-commit pytest validation and
post-commit AI-assisted unit testing workflows.

Helper scripts will live in [`scripts/test/`](scripts/test/).

## Releases

See [`docs/release-0/`](docs/release-0/), [`docs/release-1/`](docs/release-1/),
and [`docs/release-2/`](docs/release-2/) for per-release planning documents, and
[`docs/release-planning.md`](docs/release-planning.md) for the overall roadmap.

## Licence

_To be decided by the team._
