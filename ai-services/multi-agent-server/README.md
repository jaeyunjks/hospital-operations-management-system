# Shared Multi-Agent Server — Release 2 Phase 2B

This host-local Python service foundation coordinates one reusable workflow:

```text
Task -> Planner -> Worker -> Reviewer -> Human Review -> Final Result
```

It is deliberately separate from `ai-services/agentic-loop/`. Phase 2B routes
each role to its own local Ollama model while retaining the Phase 2A schemas,
validation, retries, provenance, and deterministic fallback. It uses supplied
JSON evidence, performs no application or database mutation, and does not
connect to MCP, RAG, feature backends, Docker Compose, or Azure.

## Structure

```text
multi-agent-server/
├── main.py                 # terminal entry point and human-review prompt
├── orchestrator.py         # ordered hand-offs and finalisation gate
├── models.py               # explicit Planner/Worker/Reviewer JSON schemas
├── model_client.py         # role routing + Ollama validation/retries/fallback
├── audit.py                # collision-safe persistent JSON history
├── agents/                 # separate Planner, Worker, Reviewer roles
├── prompts/                # role-specific model instructions
├── samples/                # validation input only; not core architecture
└── tests/                  # deterministic coordination and audit tests
```

The evidence contract is generic: an objective, options, constraints and
unknowns. The included Staff/Shift shortage is only the first validation
scenario. Later Worker adapters can obtain evidence from MCP and RAG without
changing the orchestrator or human-review gate.

## Run

From the repository root:

```bash
python3 ai-services/multi-agent-server/main.py
```

The default Phase 1 mode remains `--model-mode deterministic` and is reported as
`deterministic_fallback`; it is a repeatable, evidence-driven fallback and not
a live LLM result. At the prompt, choose `Approve`, `Correct`, `Reject`, or
`Partially Accept`. `Correct` requires a correction note. The audit checkpoint
is saved before the prompt, and the workflow stays `awaiting_human_review` if
no decision is entered.

For the Phase 2B live Ollama workflow:

```bash
python3 ai-services/multi-agent-server/main.py \
  --model-mode ollama \
  --max-model-attempts 3
```

`--model-mode auto` tries Ollama first and records the reason if it falls back
to deterministic execution. The default live role mapping is:

| Role | Ollama model |
|------|--------------|
| Planner | `llama3.1:8b` |
| Worker | `llama3.2:3b` |
| Reviewer | `deepseek-r1:8b` |

Override roles independently with `--planner-model`, `--worker-model`, and
`--reviewer-model`, or with `HOMS_MULTI_AGENT_PLANNER_MODEL`,
`HOMS_MULTI_AGENT_WORKER_MODEL`, and `HOMS_MULTI_AGENT_REVIEWER_MODEL`.
`OLLAMA_MODEL` and the legacy `--model` remain supported as lower-priority
global overrides. The Ollama URL default remains
`OLLAMA_URL=http://127.0.0.1:11434`.

Every live agent response, regardless of model, passes through the same
conservative JSON normalisation and explicit role JSON Schema. The client
accepts direct JSON, one surrounding JSON code fence, or one JSON object with
minimal surrounding prose; it never coerces wrong field types or semantic
values. Invalid output receives a corrective prompt containing the validation
error and required schema. The default limit is three attempts per agent
(configurable from 1 to 5). Exhaustion fails `ollama` mode; `auto` mode may use
the deterministic fallback but records that fact and all failed live attempts.

Use another scenario without changing server code:

```bash
python3 ai-services/multi-agent-server/main.py \
  --task "Evaluate the supplied operational options" \
  --evidence /absolute/path/to/evidence.json
```

Each run creates a unique file under `reports/multi-agent/` containing the
original inputs, every agent hand-off/output, raw model attempts, normalisation,
validation errors, model/provider provenance, human decision, timestamps,
final status, and final result. Existing histories are never reused or
overwritten. A successful live stage is labelled `live_ollama`; fallback output
is labelled `deterministic_fallback` and never presented as live.

## Test

```bash
python3 -m pytest -q ai-services/multi-agent-server/tests
```

The tests mock Ollama and need no model, server, database, or third-party
package. They cover schemas, normalisation, corrective retries, exhaustion,
provenance, sequential hand-offs, and the human-review gate.
