# Release 2 Contribution Log — Student 3

| | |
|---|---|
| Student | Tirth Patel (GitHub `tirth676`) |
| Feature | Pharmacy & Medication Inventory Management (`student-3/`) |
| Feature branch | `pharmacy-inventory-management`, merged into `develop` |
| Release 2 period | 19 October 2026 – |
| Release 2 commits | Listed below; merged into `develop` on 19 October |

Commit links: `https://github.com/jaeyunjks/hospital-operations-management-system/commit/<id>`.

## Summary

| Brief responsibility | Outcome |
|---|---|
| Multi-Agent integration | "Reorder decision review" on the pharmacy dashboard: the backend sends backend-calculated evidence for one low-stock medicine to the shared Multi-Agent Server, which runs Planner → Worker → Reviewer; a Pharmacy Manager then approves, corrects, partially accepts or rejects. Pharmacists can start and read a review but not decide. The workflow never creates or changes a purchase order. |
| Release 1 integration retained | AI-Mode, MCP (stock and purchase-order alerts) and RAG (pharmacy assistant) unchanged and still tested. Each reorder review also sends the agents live MCP purchase-order alerts and cited RAG answers on the reorder and approval rules. |
| Review history | "Recent reviews" table: the latest pharmacy reviews with status and the manager's comment, each reopenable (`GET /api/multi-agent/workflows`) |
| Post-commit CI/CD endpoint tests | `student-3/tests/test_endpoints_ci.py`: `POST /api/stock/issue` (FEFO) and `POST /api/purchase-orders/{id}/approve` (manager approval), 6 tests run against the backend container in `student-3.yml` |
| GitHub Actions workflow | `student-3.yml` also runs the endpoint tests and checks that Multi-Agent is retained but disabled (backend 503, dashboard "Multi-Agent disabled") |
| Pre-commit security testing | Ruff security rules, detect-secrets and pip-audit on the pharmacy services and the shared components Student 3 owns. Flask upgraded from 3.0.3 to 3.1.3 for a known vulnerability; configured service URLs restricted to http(s); SQL and bind findings reviewed and documented. With the team pre-commit configuration (root `.pre-commit-config.yaml`, `ruff.toml`) all three hooks pass on the 173 Student 3 files. |
| Agentic-loop review evidence | Multi-Agent Workflow Review of the pharmacy workflow: 9/9 checks passed; of the 22 October review with MCP and RAG context: PASS, 12/13 (the advisory MA9 records that all three stages used the rule-based fallback). Testing Review on the `student-3-junit` report and run summary from GitHub: 10/10 checks passed (team pre-commit scans ran and passed, report present, 6/6 endpoint tests passed, own `student-3.yml` run succeeded). The team hook titles were renamed to include `detect-secrets` and `pip-audit` so the review recognises them. |
| Local deployment | All 16 Compose containers healthy with Ollama, MCP (8000), RAG (8100) and the Multi-Agent Server (8200) in separate host terminals; one full review recorded through the frontend with the log lines from each terminal (`docs/ai-evidence/student-3/local-deployment-validation.txt`) |
| Cloud readiness | New `AI_ENABLED` switch; with AI-Mode, MCP, RAG and Multi-Agent all off (the cloud baseline) every pharmacy page and create/read/update/delete action works |

## Commits

| Date | Commit | Change |
|---|---|---|
| 19 Oct | [`2357fb4`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/2357fb4) | Multi-Agent reorder review (backend client, evidence builder, routes, dashboard panel, tests), post-commit endpoint tests and workflow steps, security fixes (Flask 3.1.3, URL checks), `AI_ENABLED` switch, Compose settings, READMEs and evidence |

## Integration and validation

- Live run through the pharmacy frontend in Docker: all three agents ran on local models
  (`llama3.1:8b` Planner and Reviewer, `llama3.2:3b` Worker) in about 70 seconds; a Pharmacy
  Manager correction was recorded and a second decision was refused. With `deepseek-r1:8b` as
  Reviewer the same run took 8 minutes and failed on invalid JSON, so the demo uses `llama3.1:8b`.
- 21–22 October: the shared Multi-Agent Server became asynchronous (HTTP 202, then polling) and
  added stricter semantic checks. The pharmacy backend now accepts 202, sends its facts as
  top-level evidence keys, and the dashboard polls every 3 seconds and labels rule-based fallback
  stages; with the server in `auto` mode a review reaches human review in about 85 seconds.
  A missing correction comment keeps the agents' results and the form on screen.
- Unit tests: 128 passing (database 4, backend 63, frontend 61), on Python 3.12 and Flask 3.1.3.
- `student-3.yml` and `integration-ci` passed on `pharmacy-inventory-management` and on `develop`
  for `2357fb4`, including the endpoint-test and Multi-Agent steps:
  [student-3 run](https://github.com/jaeyunjks/hospital-operations-management-system/actions/runs/39202088578).
- Cloud-baseline check: backend and frontend run with every AI feature off; pages load, CRUD
  works and the advisories use their rule-based fallback.

| Evidence | Location |
|---|---|
| Pre-commit security scan report | `docs/ai-evidence/student-3/pre-commit-security-scan.txt` |
| Team pre-commit hooks on Student 3 files | `docs/ai-evidence/student-3/pre-commit-group-hooks.txt` |
| Multi-Agent Workflow Review (agentic loop) | `docs/agent-logs/student-3/*-multi-agent-review.md` / `.json` |
| Multi-Agent workflow audit with MCP and RAG context | `reports/multi-agent/workflow-897b2780-5a15-4148-bf91-cc7bfd6ff704.json` |
| Testing Review (agentic loop), 10/10 | `docs/agent-logs/student-3/20261022T054741.600189Z-testing-review.md` / `.json` |
| Local deployment with multiple terminals | `docs/ai-evidence/student-3/local-deployment-validation.txt` |
| Testing Review inputs (CI JUnit report, run summary, pre-commit run) | `docs/test-evidence/student-3/` |
| Post-commit endpoint test run | `docs/ai-evidence/student-3/post-commit-endpoint-tests.txt` |
| Multi-Agent workflow audit (Planner, Worker, Reviewer, human review) | `reports/multi-agent/workflow-bfe70705-87c4-49c2-b372-50e7031594bb.json` |
| Feature documentation | `student-3/README.md`, `student-3/backend/README.md`, `student-3/frontend/README.md`, `student-3/tests/README.md` |

## Outstanding (group dependencies)

- Cloud deployment of the pharmacy services once the group selects Azure or AWS and
  `cloud-deployment.yml` is implemented; then the Cloud Deployment Review evidence.
- With the shared server's stricter semantic checks, the small local models' answers often fail
  them, so in `auto` mode some or all stages use the labelled rule-based fallback.
