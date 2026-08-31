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
