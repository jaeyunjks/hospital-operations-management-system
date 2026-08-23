# Student 5 AI Prompt Artefacts

**Feature:** Staff & Shift Management
**Owner:** Student 5

This directory contains the **AI prompt artefacts** used during development of
the Student 5 feature. Each file records the prompts issued to AI tooling, the
context supplied with them, and how the resulting output was validated.

## Why these are maintained

These artefacts are kept as **evidence for the prompt engineering and context
management requirements** of the ASD project. They document:

- how prompts were designed and refined during development;
- what context was provided to the AI at each step;
- what constraints were placed on AI output;
- how AI output was validated before being accepted into the codebase.

They are referenced in the technical report and are maintained alongside the
agent workflow logs in [`docs/agent-logs/student-5/`](../../agent-logs/student-5/),
which record Plan → Act → Observe → Adapt execution.

## Contents

| File | Scope |
|------|-------|
| [`database-development.md`](database-development.md) | Database microservice design and development prompts |
| [`backend-development.md`](backend-development.md) | Flask backend/API microservice development prompts |
| [`frontend-development.md`](frontend-development.md) | HTMX frontend microservice development prompts |
| [`ai-integration.md`](ai-integration.md) | AI-Mode integration prompts (Ollama + approved LLM) |
| [`code-review.md`](code-review.md) | AI-assisted code review prompts |

## Entry structure

Every recorded prompt follows the same structure:

- **Purpose** — what the prompt was intended to achieve
- **Context** — context supplied to the AI
- **Task Instructions** — the instruction as issued
- **Constraints** — boundaries placed on the response
- **Expected Output** — the anticipated form and content
- **Validation Criteria** — how the output was checked before acceptance

## Status

All prompt files currently contain **template structures only**. Entries are
added as development proceeds.
