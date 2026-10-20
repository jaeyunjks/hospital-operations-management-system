# Shared Agentic Loop

One Plan → Act → Observe → Adapt loop, run locally (never containerised), with
three validation modes and three Release 2 review modes selected by `--mode`:

| Mode | Release | ACT exercises | Implementation | Evidence file |
|---|---|---|---|---|
| `command` (default) | R0 | A validation command in `student-N/` | `agentic_loop.py` | `*-agentic-loop.md/.json` |
| `mcp` | R1 | Tools on the shared MCP server | `grounded_loop.py` | `*-grounded-loop.md/.json` |
| `rag` | R1 | Queries to the shared RAG server | `rag_loop.py` | `*-rag-validation.md/.json` |
| `multi-agent` | R2 | Multi-Agent workflow history | `multi_agent_review.py` | `*-multi-agent-review.md/.json` |
| `testing` | R2 | Pre-commit scans + CI/CD endpoint tests | `testing_review.py` | `*-testing-review.md/.json` |
| `cloud` | R2 | Probes to the Azure/AWS deployment | `cloud_review.py` | `*-cloud-deployment-review.md/.json` |

```bash
python3 ai-services/agentic-loop/agentic_loop.py --student 3 --command "python3 -m pytest -q backend/tests"
python3 ai-services/agentic-loop/agentic_loop.py --mode mcp --student 3 \
  --question "Which medicines are at or below their reorder level?" --tool homs_pharmacy_stock_alerts
python3 ai-services/agentic-loop/agentic_loop.py --mode rag --student 3
```

`--mode X --help` lists that mode's options. Evidence is written to
`docs/agent-logs/student-N/`. Each mode is described below.

---

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

# Release 1 RAG Validation Mode

`--mode rag` (`rag_loop.py`) validates one feature's use of the shared
[HOMS RAG server](../rag-server/README.md):

1. **PLAN** reads the feature's indexed document titles and section headings
   (`GET /sources`) and asks Llama for answerable questions plus one
   out-of-scope question. A fixed off-topic control question is always added.
   If the model is unavailable, section-based questions are used instead.
2. **ACT** sends every question to `POST /query` for that feature.
3. **OBSERVE** records status, confidence, citations and retrieval scores
   unchanged.
4. **CHECK** applies deterministic rules: an answerable question passes only
   when it is answered with at least one citation from the feature's or the
   shared knowledge and a valid confidence category; an out-of-scope question
   passes only when the server returns `insufficient_context` with no
   citations.
5. **ADAPT** reviews the results. It may rephrase failed answerable questions
   once; those retries form round 2 (at most two rounds). It also recommends
   one next action.

The verdict always comes from CHECK, never from the model. A feature with no
indexed documents fails, because nothing answerable can be validated.

## Run

Start Ollama and the RAG server (`python3 ai-services/rag-server/server.py`),
then from the repository root:

```bash
python3 ai-services/agentic-loop/agentic_loop.py --mode rag --student 3
python3 ai-services/agentic-loop/agentic_loop.py --mode rag --student 3 \
  --question "Who can write off an expired batch?" --question "PO"
```

`--question` (repeatable) replaces generated answerable questions, which makes
runs reproducible; `--questions N` sets how many to generate (1–6).
`HOMS_RAG_URL`, `OLLAMA_URL` and `OLLAMA_MODEL` override the defaults.

Exit codes: `0` all checks passed, `1` a check failed, `2` checks passed but
a model stage was unavailable (or bad configuration), `3` RAG server
unavailable.

Captured example (`docs/agent-logs/student-3/`): the too-short question "PO"
scored below the relevance threshold and was correctly refused in round 1;
ADAPT rephrased it to "What is the procedure for PO (Purchase Order)?", which
was answered with citations in round 2.

## Test

`tests/test_rag_loop.py` uses a fake RAG server and a scripted model. It
covers case building and fallbacks, every check rule, the ADAPT retry round
and its limits, evidence files, exit codes and the `--mode` dispatcher.

## Known limitations

- The MCP-mode grounding check verifies that cited calls exist and numbers
  appear in the evidence; it does not check completeness (for example, an
  answer may list only the 10 rows a tool returned when the tool's count is
  higher).
- Generated questions depend on the model; use `--question` for repeatable
  demonstrations.

---

# Release 2 Review Modes

The three Release 2 modes share one shape (`review_common.py`):

1. **PLAN** lists the evidence to collect and the checks to apply.
2. **ACT** collects the evidence (files, the Multi-Agent Server, or the deployed app).
3. **OBSERVE** parses it and records it unchanged.
4. **CHECK** applies deterministic rules. `required` checks decide the verdict;
   `advisory` checks are reported as warnings only.
5. **ADAPT** asks Llama for an assessment and prioritised recommendations.

The verdict always comes from CHECK, never from the model. Evidence goes to
`docs/agent-logs/group/` (or `docs/agent-logs/student-N/` with `--student N`).
All three modes keep working when Ollama is down: the checks still run and the
evidence is saved, with exit code `2` instead of `0`.

Exit codes: `0` all required checks passed, `1` a required check failed,
`2` checks passed but the model was unavailable (or bad configuration),
`3` the evidence source (Multi-Agent Server or deployed app) was unreachable.

Examples to try every mode before the real evidence exists are in
[`examples/`](examples/).

## Multi-Agent Workflow Review (`--mode multi-agent`)

```bash
python3 ai-services/agentic-loop/agentic_loop.py --mode multi-agent \
  --workflow ai-services/agentic-loop/examples/multi-agent-workflow.example.json
# or straight from the running Multi-Agent Server:
python3 ai-services/agentic-loop/agentic_loop.py --mode multi-agent --workflow-id wf-20261021-0001
```

`--workflow` and `--workflow-id` are repeatable. `--workflow-id` calls
`GET {HOMS_MULTI_AGENT_URL}/workflows/<id>` (default `http://127.0.0.1:8200`);
a bare workflow or the backends' `{"success", "data"}` envelope are both accepted.

**Workflow history contract** (what the Multi-Agent Server must export):

```json
{
  "workflow_id": "wf-...", "feature": "student-4", "request": "...", "status": "completed",
  "steps": [
    {"agent": "planner",  "started_at": "...", "finished_at": "...",
     "output": {"plan": ["..."], "required_evidence": ["..."]}},
    {"agent": "worker",   "output": {"result": "...", "evidence_used": ["..."]}},
    {"agent": "reviewer", "output": {"verdict": "...", "risks": ["..."], "recommendations": ["..."]}},
    {"agent": "human",    "output": {"decision": "approved|corrected|partially_accepted|rejected",
                                     "reviewer": "name", "comment": "..."}}
  ],
  "audit_log": [{"timestamp": "...", "actor": "planner|worker|reviewer|human", "event": "..."}]
}
```

| Check | Rule |
|---|---|
| MA1 | Planner before Worker, the last Worker output is reviewed, Human Review is last |
| MA2 | Planner gives a plan and required evidence |
| MA3 | Worker gives a result and cites evidence |
| MA4 | Worker cites every required evidence item (advisory) |
| MA5 | Reviewer gives a verdict, risks and recommendations |
| MA6 | Valid human decision with a reviewer; corrected / partially_accepted / rejected need a comment |
| MA7 | Audit log entries are complete, ordered, and cover all four actors |
| MA8 | `status` is `completed` (or `rejected` after a rejection) (advisory) |

Repeated Worker/Reviewer cycles are allowed; `human_review` is accepted as an
alias for `human`.

## Testing Report Review (`--mode testing`)

Save the evidence, then run the review:

```bash
pre-commit run --all-files 2>&1 | tee docs/test-evidence/pre-commit.txt
# each student-x.yml runs: python -m pytest <tests> --junitxml=junit.xml and uploads it
gh run download <run-id> -n student-4-junit -D docs/test-evidence/student-4
gh run view <run-id> --json workflowName,conclusion,status,headSha,headBranch,event,url,createdAt \
  > docs/test-evidence/student-4/student-4-run.json

python3 ai-services/agentic-loop/agentic_loop.py --mode testing
```

With no file arguments the mode discovers `docs/test-evidence/pre-commit*.txt`,
`docs/test-evidence/student-N/*.xml` and `docs/test-evidence/student-N/*run*.json`.
Files can also be given explicitly: `--precommit PATH`, `--junit student-4=PATH`,
`--run student-4=PATH` (repeatable). `--students 1,2,3,4,5` (default) lists who
must have CI/CD evidence.

| Check | Rule |
|---|---|
| T1 | A pre-commit report exists and has hook results |
| T2 | Ruff (or bandit), detect-secrets (or gitleaks/trufflehog) and pip-audit (or safety/osv-scanner) ran, not skipped |
| T3 | Each of those scans passed (other failed hooks are an advisory warning) |
| T4 | Every expected student has a JUnit report |
| T5 | At least 2 passing tests and no failures/errors per student |
| T6 | The run is that student's own `student-N.yml` and concluded `success` (advisory when not supplied) |

## Cloud Deployment Review (`--mode cloud`)

Copy [`examples/cloud-probes.example.json`](examples/cloud-probes.example.json),
put in the deployed URLs, then:

```bash
gh run view <run-id> --json workflowName,conclusion,status,headSha,url,createdAt,jobs > cloud-run.json
python3 ai-services/agentic-loop/agentic_loop.py --mode cloud \
  --probes docs/test-evidence/cloud-probes.json --workflow-run cloud-run.json \
  --report docs/reports/cloud-deployment.md --allow-writes
```

Probe fields: `id`, `kind` (`core`, `crud`, `ai_disabled`), `method`, `url`
(`${name}` uses `base_urls`), `body`, `expect_status`, `expect_json`
(dotted path → value), `expect_contains`, `save` (keep a response field, such
as a new record id, for later probes as `{name}`), `service` (for
`ai_disabled`: `ai-mode`, `mcp`, `rag`, `multi-agent`) and `headers`.
Header values may use `${env:NAME}` so tokens stay out of the repository;
header values are never written to the evidence.

Write probes (POST/PUT/PATCH/DELETE) only run with `--allow-writes`, because
they change the cloud database. ADAPT may ask to re-probe failed read-only
probes once after `--retry-wait` seconds (default 20), for example a container
app scaled to zero.

After ADAPT the reviewer records the **final human release decision**:
`--decision release|hold|rollback --reviewer NAME --reason TEXT`, or the CLI
asks interactively. Without either it is recorded as `pending`. A release
despite failed checks is flagged in the evidence.

| Check | Rule |
|---|---|
| C1 | Every core probe passes |
| C2 | Every CRUD probe passes (needs `--allow-writes`) |
| C3 | AI-Mode, MCP, RAG and the Multi-Agent Server each have a passing "disabled" probe |
| C4 | `cloud-deployment.yml` concluded `success`; validation jobs finished before deploy (advisory) |
| C5 | All probed endpoints use HTTPS (advisory) |

## Test

```bash
python3 -m pytest -q ai-services/agentic-loop/tests
```

`test_multi_agent_review.py`, `test_testing_review.py` and
`test_cloud_review.py` use scripted models, the example evidence and a fake
deployed backend; no Ollama, server or cloud account is needed.
