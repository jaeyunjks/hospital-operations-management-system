# Release 1 — MCP, RAG and Agentic Loop Integration

Release 1 extends the integrated Release 0 application with one shared local MCP
server, one shared local RAG server and MCP and RAG validation modes for the
shared agentic loop. All three run on the host and are never Docker Compose
services; feature backends reach them through `host.docker.internal`, and the
frontends reach them only through their own backend.

## Shared components

| Component | Location | Port | Documentation |
|---|---|---|---|
| MCP server | `ai-services/mcp-server/` | 8000 | [README](../../ai-services/mcp-server/README.md): registered tools, input/output contracts, access boundaries |
| RAG server | `ai-services/rag-server/` | 8100 | [README](../../ai-services/rag-server/README.md): API contract, grounding and confidence rules, knowledge base |
| Agentic loop | `ai-services/agentic-loop/` | — | [README](../../ai-services/agentic-loop/README.md): `--mode command|mcp|rag` |

## Feature integration

Each feature documents its MCP and RAG access, configuration and validation in
its own README (`student-N/README.md`), and keeps MCP and RAG disabled in its
`student-N.yml` workflow.

## Evidence and contribution logs

| What | Location |
|---|---|
| Contribution logs (one per student) | [`contribution-logs/`](contribution-logs/) |
| Agentic loop outputs per feature | `docs/agent-logs/student-N/` (`*-grounded-loop.*` for MCP mode, `*-rag-validation.*` for RAG mode) |
| Validation evidence | `docs/ai-evidence/` |
