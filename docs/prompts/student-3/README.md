# Student 3 AI Prompt Artefacts

**Feature:** Pharmacy & Medication Inventory Management — **Owner:** Tirth Patel

This page indexes the prompts behind the Student 3 feature: the versioned
prompts the running software sends to the local model.

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
