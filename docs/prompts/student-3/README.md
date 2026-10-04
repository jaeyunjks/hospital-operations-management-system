# Student 3 AI Prompt Artefacts

**Feature:** Pharmacy & Medication Inventory Management — **Owner:** Tirth Patel

This page indexes the prompts behind the Student 3 feature: the versioned
prompts the running software sends to the local model, and a summary of the
development prompts given to the AI coding assistant during Release 1.

## Runtime prompts (sent to the local Ollama model)

| Prompt | Location | Used by |
|---|---|---|
| Expiry and waste advisory (v1–v3) | [`student-3/backend/prompts/`](../../../student-3/backend/prompts/) | AI-Mode expiry advisory on the Batches page |
| Reorder recommendation (v1–v2) | [`student-3/backend/prompts/`](../../../student-3/backend/prompts/) | AI-Mode reorder suggestions and the scheduled agent |
| Connectivity smoke check | [`student-3/backend/prompts/smoke_v1.md`](../../../student-3/backend/prompts/smoke_v1.md) | `GET /api/ai/health` |
| Grounded answer (v1) | [`ai-services/rag-server/prompts/grounded_answer_v1.md`](../../../ai-services/rag-server/prompts/grounded_answer_v1.md) | Shared RAG server: answer only from supplied sources, cite `[S#]`, or reply `INSUFFICIENT_CONTEXT` |
| RAG validation plan and adapt | [`ai-services/agentic-loop/prompts/`](../../../ai-services/agentic-loop/prompts/) (`rag_plan.txt`, `rag_adapt.txt`) | Shared agentic loop, `--mode rag` |

Why each backend prompt version changed is recorded in
[`student-3/backend/prompts/README.md`](../../../student-3/backend/prompts/README.md)
and the "AI advisories" section of the backend README. Prompt files are
versioned and never edited in place, so earlier behaviour stays reproducible.

## Development prompts (Release 1, summarised)

Release 1 was developed with Claude Code. These are the main requests, in the
order they were given; each result was reviewed, run and tested before it was
committed.

| Area | Request given to the assistant | How the output was checked |
|---|---|---|
| MCP tool | Build a read-only pharmacy stock-alerts tool on the shared MCP server | Tool unit tests; terminal validation with `cli.py` |
| Backend and frontend MCP access | Add backend MCP endpoints, a dashboard MCP panel, Compose settings and CI checks | Backend and frontend unit tests; `student-3.yml` run with MCP disabled |
| Shared RAG server | Build the whole shared RAG server with citations, a confidence category and an insufficient-context response | 62 unit tests with a fake Ollama; `validate.sh` terminal run |
| Agentic loop | Add a `--mode` switch and a RAG validation mode to the shared loop | Loop unit tests; captured runs in `docs/agent-logs/student-3/` |
| Pharmacy RAG access | Connect the pharmacy feature to RAG and test it in Docker | Docker integration evidence in `docs/ai-evidence/student-3/` |
| Reviews | "Check my whole Release 1 as if a tutor is checking it and flag even the slightest issue" (repeated) | Each finding was approved by me before it was fixed |
| Dashboard usability | Hide JSON and server URLs, make alert figures clickable, show only the selected list, open on the most urgent list | Frontend unit tests; checked in the browser |
| Purchase orders | Add purchase-order alerts through MCP and purchase-order questions through RAG | Tool, backend and frontend tests; Docker evidence |

Constraints given throughout: MCP, RAG and the loop stay outside Docker
Compose; the frontend reaches them only through the pharmacy backend; AI never
changes inventory; MCP and RAG stay disabled in CI.
