# Hospital Operations Management System

> **Status:** Release 1 integrated application. Student 1–5 feature services,
> shared UI, Ollama AI-Mode, shared MCP and RAG services, the shared agentic
> loop, Docker Compose, and CI workflows are implemented.

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
- One shared local **MCP** (Model Context Protocol) server.
- One shared local **RAG** (Retrieval-Augmented Generation) server.
- Grounded responses with citations, confidence and explicit
  insufficient-context handling.
- MCP and RAG validation modes in the shared agentic loop.

Ollama, MCP, RAG and the shared agentic loop run directly on the host. They are
not Docker Compose services. The root `docker-compose.yml` contains only the
shared homepage and the five frontend/backend/database feature stacks: 16
application services in total.

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
├── ai-services/           # Host-local MCP, RAG, agentic loop and AI support
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

### Default and CI mode

The root [`docker-compose.yml`](docker-compose.yml) is the authoritative
integrated deployment. MCP and RAG are disabled by default in every feature
container, so the application stack and CI workflows can start without local
MCP or RAG processes. Ollama is also host-local; it is not a Compose service.

Start the 16 application services in their default mode from the repository
root:

```bash
docker compose up -d --build
docker compose ps
```

Open the shared homepage at [http://localhost:3000](http://localhost:3000).

### Local Release 1 demo mode

Use this startup order when demonstrating MCP and RAG through the containerised
feature backends.

1. Start Ollama on the host:

   ```bash
   ollama serve
   ```

2. Start the shared MCP server in Docker-access mode:

   ```bash
   HOMS_MCP_HOST=0.0.0.0 \
   HOMS_MCP_ALLOW_DOCKER_HOST=true \
   python3 ai-services/mcp-server/server.py
   ```

3. Build the current RAG index:

   ```bash
   python3 ai-services/rag-server/ingest.py
   ```

4. Start the shared RAG server in Docker-access mode:

   ```bash
   HOMS_RAG_HOST=0.0.0.0 \
   HOMS_RAG_ALLOW_DOCKER_HOST=true \
   python3 ai-services/rag-server/server.py
   ```

5. Explicitly enable MCP and RAG for the feature containers being
   demonstrated. For Student 2, whose root-Compose variables intentionally use
   a lowercase `student2_` prefix:

   ```bash
   student2_MCP_ENABLED=true \
   student2_RAG_ENABLED=true \
   docker compose up -d student-2-backend student-2-frontend
   ```

   The corresponding enable variables for the other feature backends are:

   - `STUDENT1_MCP_ENABLED` / `STUDENT1_RAG_ENABLED`
   - `STUDENT3_MCP_ENABLED` / `STUDENT3_RAG_ENABLED`
   - `STUDENT4_MCP_ENABLED` / `STUDENT4_RAG_ENABLED`
   - `STUDENT5_MCP_ENABLED` / `STUDENT5_RAG_ENABLED`

For example, set both variables for a feature to `true` before its
`docker compose up` command. The containers reach the host services through
`host.docker.internal`; their frontends continue to call only their own
backends.

## Architecture

See [`docs/architecture/`](docs/architecture/). High-level: five independent
microservice sets use the shared homepage and host-local Ollama, MCP and RAG
services. Frontends call only their own backends; the backends reach the shared
services through configured HTTP boundaries. The shared Plan → Act → Observe →
Adapt workflow is under [`ai-services/agentic-loop/`](ai-services/agentic-loop/).

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
