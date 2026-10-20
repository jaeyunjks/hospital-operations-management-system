# Shared Multi-Agent Server — Release 2 HTTP Interface

This host-local Python service foundation coordinates one reusable workflow:

```text
Task -> Planner -> Worker -> Reviewer -> Human Review -> Final Result
```

It is deliberately separate from `ai-services/agentic-loop/`. The service
routes each role to its own local Ollama model while retaining the Phase 2A
schemas, validation, retries, provenance, and deterministic fallback. The CLI
and HTTP API use the same orchestrator, agents, Human Review rules, and audit
store. It uses supplied JSON evidence, performs no application or database
mutation, and does not connect to MCP, RAG, feature backends, Docker Compose,
or Azure.

## Structure

```text
multi-agent-server/
├── api.py                  # local Flask HTTP interface
├── main.py                 # terminal entry point and human-review prompt
├── orchestrator.py         # ordered hand-offs and finalisation gate
├── models.py               # explicit Planner/Worker/Reviewer JSON schemas
├── model_client.py         # role routing + Ollama validation/retries/fallback
├── semantic_validation.py  # generic evidence/constraint cross-stage checks
├── audit.py                # collision-safe persistent JSON history
├── agents/                 # separate Planner, Worker, Reviewer roles
├── prompts/                # role-specific model instructions
├── samples/                # validation input only; not core architecture
├── requirements.txt        # explicit HTTP runtime dependency
└── tests/                  # deterministic coordination and audit tests
```

The evidence contract is generic: an objective, options, constraints and
unknowns. The included Staff/Shift shortage is only the first validation
scenario. Later Worker adapters can obtain evidence from MCP and RAG without
changing the orchestrator or human-review gate.

## Run

Install the HTTP dependency in your environment:

```bash
python3 -m pip install -r ai-services/multi-agent-server/requirements.txt
```

### Terminal workflow

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

Schema-valid output also passes deterministic semantic validation before it is
accepted. Planner evidence requirements are unique supplied top-level keys.
Worker option IDs, labels, hard-constraint results, eligibility, selection,
objective gap, and safely derivable projections are checked against supplied
structured evidence. The currently supported hard-constraint operator is
`equals`; unknown operators fail closed. Advisory risks and retrieved policy
context never become eligibility rules unless the input explicitly represents
them as `type=hard`, `scope=option` constraints.

Reviewer recommendations use `recommended_option_id` (`string` or `null`) and
must agree with the validated Worker selection. Deterministically validated
facts use `validated_evidence_refs` entries containing `source`, `path`, and the
exact `value`; unrestricted prose is not accepted as a validated finding.
Recommendation prose is limited to safe generic review instructions; all option
identity is carried by the semantically checked `recommended_option_id` field.
Semantic failures use the existing bounded correction attempts and `auto` mode
fallback. Persisted execution distinguishes `malformed_json`, `schema_invalid`,
and `semantic_invalid`, and records compact issue codes plus retry/fallback
status. The stable public HTTP execution summary is unchanged.

Use another scenario without changing server code:

```bash
python3 ai-services/multi-agent-server/main.py \
  --task "Evaluate the supplied operational options" \
  --evidence /absolute/path/to/evidence.json
```

Each run creates a unique file under `reports/multi-agent/` containing the
original inputs, every agent hand-off/output, raw model attempts, normalisation,
validation errors, model/provider provenance, human decision, timestamps,
semantic validation metadata, final status, and final result. Existing
histories are never reused or overwritten. A successful live stage is labelled
`live_ollama`; fallback output
is labelled `deterministic_fallback` and never presented as live.

### HTTP workflow

Start the local server from the repository root. The default is loopback-only
on port `8200`:

```bash
python3 ai-services/multi-agent-server/api.py \
  --model-mode ollama \
  --planner-model llama3.1:8b \
  --worker-model llama3.2:3b \
  --reviewer-model deepseek-r1:8b \
  --max-model-attempts 3 \
  --ollama-timeout 180
```

The HTTP API exposes:

| Method | Path | Result |
|--------|------|--------|
| `GET` | `/health` | Lightweight service health; never calls Ollama |
| `POST` | `/workflows` | Persists and returns a new `in_progress` workflow immediately |
| `GET` | `/workflows/{workflow_id}` | Polls current persisted stage/lifecycle state |
| `POST` | `/workflows/{workflow_id}/review` | Records Human Review and finalises the workflow |

Create a generic workflow:

```bash
jq -n \
  --arg task "Resolve a staffing shortage for the Emergency evening shift." \
  --slurpfile evidence ai-services/multi-agent-server/samples/staff-shortage.json \
  '{task: $task, evidence: $evidence[0]}' \
| curl --fail-with-body \
    -H 'Content-Type: application/json' \
    --data-binary @- \
    http://127.0.0.1:8200/workflows
```

The create request returns HTTP `202`, the real workflow UUID, `Location`, and
`Retry-After: 1` after the initial `in_progress` audit record is durable. A
bounded background worker then runs the unchanged Planner → Worker → Reviewer
sequence. Poll `Location` until the status becomes `awaiting_human_review` or
`failed`; completed stages become visible as they are persisted. In-progress
records are resumed from the next incomplete stage when the server restarts.
The single background worker bounds local model load; additional workflows
remain durably `in_progress` until their turn. No human decision is inferred.
Retrieve the state and then review it:

```bash
curl --fail-with-body http://127.0.0.1:8200/workflows/WORKFLOW_ID

curl --fail-with-body \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve","comment":"Approved by staff manager."}' \
  http://127.0.0.1:8200/workflows/WORKFLOW_ID/review
```

Review decisions are `approve`, `correct`, `reject`, and `partially_accept`.
`correct` requires a non-blank `comment`. Unknown workflows return `404`, and
reviewing a workflow before its review gate or after completion returns `409`.
The JSON audit files remain the source of truth, so workflows can be retrieved
and reviewed after a server restart without a separate database.

Server configuration uses `HOMS_MULTI_AGENT_HOST` and
`HOMS_MULTI_AGENT_PORT`. A wildcard bind requires the explicit
`HOMS_MULTI_AGENT_ALLOW_DOCKER_HOST=true` opt-in, matching the shared RAG
service convention. Authentication and authorisation are intentionally deferred
to a later integration/security phase. Do not expose this development server
outside a trusted local environment.

## Test

```bash
python3 -m pytest -q ai-services/multi-agent-server/tests
```

The tests mock Ollama and need no live model, database, or feature service.
They cover schemas, normalisation, corrective retries, exhaustion, provenance,
sequential hand-offs, persisted HTTP lifecycle, API validation, and the Human
Review gate. Generic semantic tests additionally cover Planner evidence
resolution, Worker option/constraint fidelity, structured Reviewer grounding,
safe fallback, and audit metadata.
