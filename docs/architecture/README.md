# Architecture

_Placeholder — to be expanded by the team._

The Hospital Operations Management System is composed of **five independently
owned microservice sets** (`student-1` .. `student-5`), each providing a
frontend, a Flask REST API, and a SQLite database. These are integrated behind
a **shared frontend** (`shared/frontend/`) and a **shared AI mode**, with
central **AI services** (`ai-services/`) providing Ollama-backed inference and,
in later releases, MCP / RAG / Multi-Agent capabilities.

## Documents in this folder

- [`feature-ownership.md`](feature-ownership.md) — which student owns which feature area.
- [`api-data-ownership.md`](api-data-ownership.md) — API and data ownership boundaries.
- [`design-system.md`](design-system.md) — shared UI theme / design system.
- [`development-workflow.md`](development-workflow.md) — how the team works in this repo.

## Diagrams

_To be added (component diagram, deployment diagram, data flow)._ Do not
manufacture architecture details that have not been agreed by the team.
