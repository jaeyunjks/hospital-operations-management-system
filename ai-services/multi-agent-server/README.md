# Shared Multi-Agent Server — Release 2 Phase 1

This host-local Python service foundation coordinates one reusable workflow:

```text
Task -> Planner -> Worker -> Reviewer -> Human Review -> Final Result
```

It is deliberately separate from `ai-services/agentic-loop/`. Phase 1 uses
supplied JSON evidence, performs no application or database mutation, and does
not connect to MCP, RAG, feature backends, Docker Compose, or Azure.

## Structure

```text
multi-agent-server/
├── main.py                 # terminal entry point and human-review prompt
├── orchestrator.py         # ordered hand-offs and finalisation gate
├── models.py               # shared workflow contracts
├── model_client.py         # Ollama interface + deterministic fallback
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

## Run the repeatable Phase 1 demonstration

From the repository root:

```bash
python3 ai-services/multi-agent-server/main.py
```

The default `--model-mode deterministic` is explicitly reported as
`deterministic_fallback`; it is a repeatable, evidence-driven fallback and not
a live LLM result. At the prompt, choose `Approve`, `Correct`, `Reject`, or
`Partially Accept`. `Correct` requires a correction note. The audit checkpoint
is saved before the prompt, and the workflow stays `awaiting_human_review` if
no decision is entered.

For a local Ollama call using the same role prompts and output contracts:

```bash
python3 ai-services/multi-agent-server/main.py --model-mode ollama
```

`--model-mode auto` tries Ollama first and records the reason if it falls back
to deterministic execution. Defaults follow the existing project convention:
`OLLAMA_MODEL=llama3.1:8b` and `OLLAMA_URL=http://127.0.0.1:11434`.

Use another scenario without changing server code:

```bash
python3 ai-services/multi-agent-server/main.py \
  --task "Evaluate the supplied operational options" \
  --evidence /absolute/path/to/evidence.json
```

Each run creates a unique file under `reports/multi-agent/` containing the
original inputs, every agent hand-off/output and execution mode, human decision,
timestamps, final status, and final result. Existing histories are never
reused or overwritten.

## Test

```bash
python3 -m pytest -q ai-services/multi-agent-server/tests
```

The tests need no model, server, database, or third-party package.
