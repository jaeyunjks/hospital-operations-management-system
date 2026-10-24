# Hospital Operations Management System

> **Status:** Release 2 integrated application. Student 1–5 feature services,
> shared UI, local AI services, the shared Multi-Agent workflow, Azure
> deployment, and GitHub Actions validation/deployment workflows are
> implemented.

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

The Release 2 cloud foundation targets **Microsoft Azure Container Apps**. It
uses one shared environment with uniquely named per-student services and a
manual, scoped deployment workflow. See
[`docs/release-2/azure-deployment.md`](docs/release-2/azure-deployment.md).

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

- Integrated local application through the root Docker Compose stack.
- Shared host-local **AI-Mode**, **MCP**, **RAG**, and **Multi-Agent Server**.
- A reusable **Planner → Worker → Reviewer → Human Review** workflow
  with persistent audit history and deterministic fallback after failed model
  output validation.
- Pre-commit security checks and student/integration GitHub Actions workflows.
- Reusable, per-student deployment to **Azure Container Apps** through Azure
  Container Registry.
- A safe cloud baseline where AI-Mode, MCP, RAG, and Multi-Agent integrations
  are explicitly disabled unless a separate cloud extension is implemented.

The shared AI services remain local and non-containerised. Feature backends
provide the operational integration layer and retain responsibility for their
own business rules and data mutations.

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

### Local Release 2 AI services

Use the host-local AI services when demonstrating AI-Mode, MCP, RAG, or the
Multi-Agent workflow through a containerised feature backend. Student 5 has a
single launcher that starts its application slice and the required local
services:

```bash
./scripts/start-student5-r2.sh
```

Use `./scripts/stop-student5-r2.sh` to stop that local stack. The manual setup
below remains useful for other feature-specific demonstrations.

Before the first run, install the shared servers' Python dependencies and pull
the local models (repository root):

```bash
python3 -m pip install \
  -r ai-services/mcp-server/requirements.txt \
  -r ai-services/rag-server/requirements.txt \
  -r ai-services/multi-agent-server/requirements.txt
```

```bash
ollama pull llama3.1:8b
ollama pull llama3.2:3b
ollama pull qwen2.5:3b
ollama pull deepseek-r1:8b
ollama pull nomic-embed-text
```

| Model | Used by |
|---|---|
| `llama3.1:8b` | Student 2 and Student 5 AI-Mode; agentic loop; Multi-Agent Planner |
| `llama3.2:3b` | Student 3 AI-Mode; RAG server answers; Multi-Agent Worker |
| `qwen2.5:3b` | Student 1 and Student 4 AI-Mode |
| `deepseek-r1:8b` | Multi-Agent Reviewer |
| `nomic-embed-text` | RAG server embeddings (`ingest.py` fails without it) |

1. Start Ollama on the host (skip this if the Ollama app is already running):

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

5. Start the shared Multi-Agent Server when the feature workflow requires it:

   ```bash
   HOMS_MULTI_AGENT_HOST=0.0.0.0 \
   HOMS_MULTI_AGENT_ALLOW_DOCKER_HOST=true \
   python3 ai-services/multi-agent-server/api.py --model-mode auto
   ```

6. Explicitly enable the applicable MCP, RAG, and Multi-Agent integrations for
   the feature container being demonstrated. For Student 2, whose root-Compose
   variables intentionally use a lowercase `student2_` prefix:

   ```bash
   student2_MCP_ENABLED=true \
   student2_RAG_ENABLED=true \
   docker compose up -d student-2-backend student-2-frontend
   ```

   The corresponding MCP/RAG enable variables for the other feature backends
   are:

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
microservice sets use the shared homepage and host-local Ollama, MCP, RAG, and
Multi-Agent services. Frontends call only their own backends; the backends
reach shared services through configured HTTP boundaries. The shared
Plan → Act → Observe → Adapt review modes are under
[`ai-services/agentic-loop/`](ai-services/agentic-loop/), while the Release 2
Planner → Worker → Reviewer → Human Review service is under
[`ai-services/multi-agent-server/`](ai-services/multi-agent-server/).

## Azure deployment

The Release 2 deployment flow is:

```text
GitHub repository
  → GitHub Actions validation
  → Docker build
  → Azure Container Registry
  → Azure Container Apps
```

Azure authentication uses GitHub OIDC and a pre-created managed identity, not
a long-lived Azure client secret. Deployments are scoped to one student's
frontend, backend, and database services. The authoritative workflow and
scripts are:

- [`.github/workflows/cloud-deployment.yml`](.github/workflows/cloud-deployment.yml)
- [`scripts/deploy/deploy-student.sh`](scripts/deploy/deploy-student.sh)
- [`scripts/deploy/deployment-testing.sh`](scripts/deploy/deployment-testing.sh)
- [`docs/release-2/azure-deployment.md`](docs/release-2/azure-deployment.md)

The baseline cloud deployment sets `AI_ENABLED`, `MCP_ENABLED`, `RAG_ENABLED`,
and `MULTI_AGENT_ENABLED` to `false`. The shared AI services are demonstrated
locally and are not deployed by this baseline.

**Student 5 Azure frontend:**
[https://s5-web.purplepebble-f0501bb8.australiaeast.azurecontainerapps.io/](https://s5-web.purplepebble-f0501bb8.australiaeast.azurecontainerapps.io/)

## Development workflow

Development is organised by feature ownership:

- Each student works within their own `student-N/` directory.
- Shared assets under `shared/` change via team agreement / review.
- Each student maintains their own `.github/workflows/student-N.yml`.

See [`docs/architecture/development-workflow.md`](docs/architecture/development-workflow.md).

## Testing

Each student maintains tests under `student-N/tests/`. Release 2 includes
pre-commit security validation, per-student post-commit endpoint workflows,
whole-application integration testing, deployment contract tests, and shared
Multi-Agent/agentic-loop tests. Student 5's post-commit workflow is
[`.github/workflows/student-5.yml`](.github/workflows/student-5.yml).

## Releases

See [`docs/release-0/`](docs/release-0/), [`docs/release-1/`](docs/release-1/),
and [`docs/release-2/`](docs/release-2/) for per-release planning documents, and
[`docs/release-planning.md`](docs/release-planning.md) for the overall roadmap.

## Licence

_To be decided by the team._
