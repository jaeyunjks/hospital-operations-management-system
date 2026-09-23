# Release 0 Agentic Loop

Shared, single-model validation workflow for any `student-x` component:

1. **PLAN** — Llama analyses the selected component, task and validation scope.
2. **ACT** — the CLI runs the exact validation command in that component.
3. **OBSERVE** — stdout, stderr, exit status and timing are captured unchanged.
4. **ADAPT** — Llama reviews the evidence and recommends one next action.

This is deliberately not the Release 2 Planner/Worker/Reviewer architecture.
It uses one Ollama model, defaults to `llama3.1:8b`, and never edits code.

## Prerequisite

```bash
ollama pull llama3.1:8b
```

Run Ollama locally, then invoke the CLI from the repository root:

```bash
python3 ai-services/agentic-loop/agentic_loop.py \
  --student 5 \
  --command "pytest -q"
```

The command runs directly, without a shell, in `student-5/`. Replace `5` with
another student number to reuse the loop for that component. Shell pipelines
and redirection are intentionally not interpreted; use a checked-in validation
script when a multi-step command is needed.

By default, timestamped Markdown and JSON evidence is saved to:

```text
docs/agent-logs/student-5/
```

Use `--task` to describe a narrower validation objective or `--logs-dir` to
choose a temporary evidence destination. `OLLAMA_MODEL` and `OLLAMA_URL` may
override the defaults when required.

The CLI returns the validation command's non-zero status when validation fails.
If validation passes but either Ollama stage is unavailable, it saves the
partial evidence and returns status `2`.

---

# Release 1 Grounded Agentic Loop

`grounded_loop.py` extends the loop for Release 1 (Grounded AI). ACT is no
longer a shell command: the model chooses tools advertised by the shared
[HOMS MCP server](../mcp-server/README.md) and every answer must be backed by
tool evidence.

1. **PLAN** discover MCP tools and give their contracts to Llama.
2. **ACT** Llama requests tool calls (Ollama tool calling).
3. **OBSERVE** each call is validated and executed through MCP; the structured
   result is recorded unchanged and returned to the model as `call-N`.
4. **ADAPT** Llama either asks for more evidence or answers in JSON, citing
   the call IDs it used.

A deterministic **grounding check** then runs without the model:

| Status | Meaning |
|---|---|
| `grounded` | Every cited call exists and succeeded, and every number in the answer appears in the cited evidence |
| `partial` | At least one valid citation, but an invalid citation or an unsupported number |
| `ungrounded` | No valid citation, or no answer |

Guard rails: step budget (`--max-steps`, default 4), tool-call budget
(`--max-tool-calls`, default 6), optional tool allowlist (`--tool`, repeatable),
unknown tools are rejected without execution, tool output is treated as
untrusted data, and transport failures are recorded as evidence instead of
crashing the run. When the budget is spent, tools are withdrawn and the model
is asked once to answer from the evidence it already has.

## Run

Start the Student 4 backend, the MCP server and Ollama, then from the
repository root:

```bash
python3 ai-services/mcp-server/server.py            # terminal 1
python3 ai-services/agentic-loop/grounded_loop.py \
  --student 4 \
  --question "How many beds are available in Emergency?"
```

Requires `mcp==2.2.0` (see `ai-services/mcp-server/requirements.txt`) and
Python 3.12. Evidence is saved as `*-grounded-loop.md/.json` in
`docs/agent-logs/student-N/`. `HOMS_MCP_URL`, `OLLAMA_URL` and `OLLAMA_MODEL`
override the defaults.

Exit codes: `0` grounded, `1` answered but not fully grounded, `2` model
unavailable or bad configuration, `3` MCP server unavailable.

## Test

```bash
python3 -m pytest -q ai-services/agentic-loop/tests
```

`test_grounded_loop.py` uses a scripted model and fake tools.
`test_grounded_loop_mcp.py` runs the loop against the real MCP server
in-process, mocking only the Student 4 HTTP API and the model.

## Not yet included

RAG is not wired in because `ai-services/rag-server/` is still a placeholder.
Once it exposes retrieval (ideally as an MCP tool), the loop discovers it
automatically with no code change.
