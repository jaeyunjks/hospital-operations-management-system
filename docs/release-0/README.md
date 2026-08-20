# Release 0 — Microservices + AI-Mode

_Placeholder planning document._

## Agreed high-level scope

- Five independent microservices (frontend + Flask API + SQLite), one per student.
- **AI-Mode** backed by **Ollama** + an approved open-source LLM (Llama / Qwen / DeepSeek).
- Agentic loop: **Plan → Act → Observe → Adapt**.
- **Docker Compose** integration of all services.
- Individual **CI/CD** workflow per student (`.github/workflows/student-N.yml`).

## To be planned

- Per-feature CRUD scope and data model.
- AI-Mode interaction design and prompt/loop details.
- Integration/acceptance criteria for the release.

Do not invent implementation details that have not been agreed.
