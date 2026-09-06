# Hospital Operations Management System

> **Status:** Release 0 integrated application. Student 1–5 feature services,
> shared UI, Ollama AI-Mode, agentic workflow, Docker Compose, and CI workflows
> are implemented.

## Project

**Hospital Operations Management System** — an integrated Agentic AI application
supporting hospital operational coordination.

The system comprises **five independently owned student feature sets**. Each
feature set contains an **HTMX frontend microservice**, a **Flask backend/API
microservice**, and a **SQLite database microservice**, integrated through the
root Docker Compose application, shared HTMX homepage, and shared AI-Mode.

## Purpose

Provide an integrated, Agentic-AI-assisted platform that supports the day-to-day
operational coordination of a hospital across admissions, clinical staffing,
medication administration, bed management, and shift management.

## Release 0 feature areas

| # | Feature area | Owner |
|---|-----------------------------------------|-------------------------|
| 1 | Patient & Admission Management          | _Jesse_ — see `docs/architecture/feature-ownership.md`_ |
| 2 | Clinical Staff Management               | _Jordan_ |
| 3 | Pharmacy & Medication Inventory Management | _Tirth_ |
| 4 | Room & Bed Management                   | _Asher_ |
| 5 | Staff / Shift Management                | _Yafie_ |

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
- Individual **CI/CD** workflows: `student-1.yml` through `student-5.yml`.
- Whole-group integration workflow: `integration-ci.yml`.

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
├── .github/workflows/     # Student CI workflows and whole-group integration CI
├── docs/                  # Architecture, reports, and per-release documentation
├── shared/                # Shared HTMX homepage, CSS/UI theme, and configuration
├── student-1 .. student-5/ # Independently owned feature microservice sets
├── ai-services/           # Shared Release 0 agentic loop and future AI services
├── scripts/               # Build / test / deploy helper scripts
└── docker-compose.yml     # Root integrated application orchestration
```

Each student's frontend, backend/API, and database are separately containerised,
with Dockerfiles in their respective service directories.

## Team members and feature ownership

| Student | Owner | Feature area |
|---------|-------|--------------|
| student-1 | Jesse | Patient & Admission Management |
| student-2 | Jordan | Clinical Staff Management |
| student-3 | Tirth | Pharmacy & Medication Inventory Management |
| student-4 | Asher | Room & Bed Management |
| student-5 | Yafie | Staff & Shift Management |

## Local setup

Prerequisites: Docker & Docker Compose, Git, and Ollama with the models named
by the active service configuration available locally. If a configured model is
unavailable, some features may use their implemented fallback behaviour until
the required model is available.

Start the integrated Release 0 application from the repository root:

```bash
docker compose up -d --build
docker compose ps
```

Open the shared homepage at [http://localhost:3000](http://localhost:3000).

## Architecture

See [`docs/architecture/`](docs/architecture/). High-level: five independent
microservice sets integrated behind a shared frontend and shared Ollama AI-Mode,
with the Release 0 Plan → Act → Observe → Adapt workflow under
[`ai-services/agentic-loop/`](ai-services/agentic-loop/).

## Development workflow

Development is organised by feature ownership:

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
