# Student 3 — Pharmacy & Medication Inventory Management

Feature microservice set for the Hospital Operations Management System (HOMS).

- Owner: **Tirth Patel** (GitHub `tirth676`)
- Feature area: **Pharmacy & Medication Inventory Management**
- Branch: `pharmacy-inventory-management`; CI workflow: `.github/workflows/student-3.yml`

## What the feature does

Hospital pharmacy staff manage medicines, batches and suppliers, issue stock
first-expiry-first-out, receive deliveries, write off expired batches and run a
manager-approved purchase-order workflow. A dashboard shows low stock, expiring
and expired batches, pending approvals and recent stock movements.

| Service | Port | Folder | Role |
|---|---|---|---|
| Frontend | 3300 | [`frontend/`](frontend/README.md) | Flask + HTMX pages; calls only the backend |
| Backend/API | 5300 | [`backend/`](backend/README.md) | Business rules, AI-Mode, MCP and RAG access; calls only the database service |
| Database | 6300 | [`database/`](database/README.md) | SQLite data-access service; the only component that opens `pharmacy.db` |

Two demonstration roles (chosen on `/demo`, not a real login): **Pharmacy
Manager** (maintains records, writes off stock, approves orders) and
**Pharmacist** (views inventory, issues and receives stock).

## Release 0 (retained)

- Medicines, suppliers, batches, stock movements and purchase orders with CSV export.
- FEFO issuing, delivery receipt, manager-only write-off, append-only stock ledger.
- AI-Mode (local Ollama, `llama3.2:3b`): read-only expiry/waste advisory and reorder
  suggestions with rule-based fallback, plus a scheduled Plan → Act → Observe → Adapt
  agent that only drafts `pending_approval` orders for a manager to review.

## Release 1 additions

Both panels are at the top of the dashboard. The frontend never contacts MCP or
RAG directly; it calls this feature's backend, which calls the shared servers.

| Feature | Frontend | Backend | Shared server |
|---|---|---|---|
| **Shared MCP tools** — live stock alerts, purchase-order alerts and a connectivity check | "Shared MCP tools" panel | `GET /api/mcp/status`, `POST /api/mcp/call` | MCP server (8000): `homs_pharmacy_stock_alerts`, `homs_pharmacy_order_alerts`, `homs_echo` |
| **Pharmacy assistant** — grounded answers with citations and a confidence category, or an insufficient-context response | "Ask the pharmacy assistant" panel | `GET /api/rag/status`, `POST /api/rag/ask` | RAG server (8100), `feature: student-3` |

- Boundaries: the backend exposes only the pharmacy MCP tools and `homs_echo` (others return
  403) and asks RAG only about `student-3` and shared knowledge; citations from any
  other feature are rejected. Both tools are read-only and never change inventory.
- Configuration: `MCP_ENABLED` / `RAG_ENABLED` (default `false`), `MCP_SERVER_URL`,
  `RAG_SERVER_URL`. Docker Compose points them at `host.docker.internal`; AI-Mode also
  uses the host's Ollama. MCP, RAG and Ollama are never containerised.
- Pharmacy knowledge base: [`ai-services/rag-server/knowledge/student-3/`](../ai-services/rag-server/knowledge/student-3/).
- Pharmacy MCP tools: [`pharmacy_stock.py`](../ai-services/mcp-server/tools/pharmacy_stock.py) and
  [`pharmacy_orders.py`](../ai-services/mcp-server/tools/pharmacy_orders.py) in `ai-services/mcp-server/tools/`.

Student 3 also built the shared RAG server (`ai-services/rag-server/`) and the
agentic loop's RAG validation mode (`ai-services/agentic-loop/rag_loop.py`).

## Release 2 additions

| Feature | Frontend | Backend | Shared server |
|---|---|---|---|
| **Reorder decision review** — Planner → Worker → Reviewer → human review for one low-stock medicine, with live MCP order alerts and RAG reorder rules as context, and a list of recent reviews | "Reorder decision review" panel and "Recent reviews" table on the dashboard | `GET /api/multi-agent/status`, `GET /api/multi-agent/candidates`, `POST /api/multi-agent/reorder-workflows`, `GET /api/multi-agent/workflows`, `GET /api/multi-agent/workflows/{id}`, `POST /api/multi-agent/workflows/{id}/review` | Multi-Agent Server (8200), plus MCP (8000) and RAG (8100) for context |

- The backend calculates every figure (stock, expiring units, open orders, usage, suggested
  quantity) and sends them to the Multi-Agent Server as evidence with three options:
  order the suggested quantity, order half, or do not order. It also adds Release 1 context: the
  pharmacy's live purchase-order alerts from the shared MCP server (`homs_pharmacy_order_alerts`;
  an overdue order for the same medicine becomes a risk) and the reorder and approval rules from
  the shared RAG server, with their sources. If MCP or RAG is off, the evidence says so and the
  review still runs. The Planner plans the check, the
  Worker evaluates the options, the Reviewer checks the proposal for risks, and the workflow
  then waits for a human decision.
- "Recent reviews" lists the latest pharmacy reviews with their status and the manager's comment;
  each can be reopened. It refreshes when a review finishes or a decision is recorded.
- Only a Pharmacy Manager can record the decision (approve, correct, partially accept or
  reject; a comment is required unless approving). Pharmacists can start a review and read it.
- Boundaries: the backend only reads or reviews workflows it started (task prefix
  `Pharmacy reorder decision:`; others return 403). The workflow never creates or changes a
  purchase order.
- Configuration: `MULTI_AGENT_ENABLED` (default `false`), `MULTI_AGENT_SERVER_URL`,
  `MULTI_AGENT_TIMEOUT` (300 s; the three local models take about 1–2 minutes).
- Cloud baseline: `AI_ENABLED=false` switches AI-Mode off (advisories use their rule-based
  fallback and Ollama is never contacted). With `MCP_ENABLED`, `RAG_ENABLED` and
  `MULTI_AGENT_ENABLED` also false, every page and create/read/update/delete action still works.
- Post-commit endpoint tests: `student-3/tests/test_endpoints_ci.py` tests
  `POST /api/stock/issue` and `POST /api/purchase-orders/{id}/approve` against the running
  backend container in `student-3.yml`.
- Pre-commit security scans (Ruff security rules, detect-secrets, pip-audit) run on every commit
  through the team configuration (root `.pre-commit-config.yaml`, `ruff.toml`; see `SECURITY.md`).
  Flask was upgraded to 3.1.3 for a known vulnerability; see
  [`docs/ai-evidence/student-3/pre-commit-security-scan.txt`](../docs/ai-evidence/student-3/pre-commit-security-scan.txt)
  and [`docs/ai-evidence/student-3/pre-commit-group-hooks.txt`](../docs/ai-evidence/student-3/pre-commit-group-hooks.txt).

## Run

Prerequisites: Docker Desktop, Python 3.12+, Ollama with `llama3.2:3b` and
`nomic-embed-text` (`llama3.1:8b` for the agentic loop).

Install the shared servers' dependencies once (repository root):

```bash
python3 -m pip install -r ai-services/mcp-server/requirements.txt -r ai-services/rag-server/requirements.txt
```

Start the shared servers on the host (repository root, one terminal each):

```bash
HOMS_MCP_HOST=0.0.0.0 HOMS_MCP_ALLOW_DOCKER_HOST=true python3 ai-services/mcp-server/server.py
```

```bash
HOMS_MULTI_AGENT_HOST=0.0.0.0 HOMS_MULTI_AGENT_ALLOW_DOCKER_HOST=true python3 ai-services/multi-agent-server/api.py --model-mode auto --max-model-attempts 1 --planner-model llama3.1:8b --worker-model llama3.2:3b --reviewer-model llama3.1:8b
```

```bash
python3 ai-services/rag-server/ingest.py
HOMS_RAG_HOST=0.0.0.0 HOMS_RAG_ALLOW_DOCKER_HOST=true python3 ai-services/rag-server/server.py
```

Then start this feature with MCP, RAG and Multi-Agent enabled:

```bash
STUDENT3_MCP_ENABLED=true STUDENT3_RAG_ENABLED=true STUDENT3_MULTI_AGENT_ENABLED=true docker compose up -d --build student-3-database student-3-backend student-3-frontend
```

Open <http://localhost:3300>, choose a role, and use the three panels at the top.
Without the `STUDENT3_*_ENABLED=true` overrides the panels report "disabled",
which is the CI configuration. To run without Docker, see each service's README.

## Test

| What | Command (repository root) | Expected |
|---|---|---|
| Unit tests | `python3 -m pytest -q student-3` (or each of `student-3/database/tests`, `backend/tests`, `frontend/tests`) | 128 passing (4, 63 and 61); the 6 endpoint tests are skipped |
| Endpoint tests | `STUDENT3_BACKEND_URL=http://localhost:5300 python3 -m unittest discover -v -s student-3/tests -p 'test_endpoints_ci.py'` against a running backend (they change data, so use a fresh database) | 6 passing |
| Multi-Agent in the UI | Choose a medicine in "Reorder decision review" and run it; then, as Pharmacy Manager, record a decision | Planner, Worker and Reviewer results, then the recorded decision |
| MCP in the UI | "Get stock alerts via MCP"; "Test connectivity" | "Valid tool result" with counts and tables; "Connection OK" |
| RAG in the UI | Ask "Who can write off an expired batch?" | Answer with a confidence badge and a cited source |
| Insufficient context | Ask "What is the capital of France?" | "Not enough information…"; no answer, no sources |
| Agentic loop | `python3 ai-services/agentic-loop/agentic_loop.py --mode mcp --student 3 --question "…"` and `--mode rag --student 3` | Grounded answer / all checks PASS |

CI (`student-3.yml`) runs the unit tests, the shared pharmacy MCP tool, RAG server and
loop RAG-mode tests, validates the Compose file, builds all three images, runs the two
post-commit endpoint tests against the backend container, and checks that MCP, RAG and
Multi-Agent are present but disabled in both the backend and the dashboard.

## Evidence

- Docker integration: [`docs/ai-evidence/student-3/docker-integration-validation.txt`](../docs/ai-evidence/student-3/docker-integration-validation.txt)
  (captured with `STUDENT3_MCP_ENABLED=true STUDENT3_RAG_ENABLED=true`, the demo switches)
- Database operations: [`docs/ai-evidence/student-3/database-verification.txt`](../docs/ai-evidence/student-3/database-verification.txt)
- Agentic loop runs (MCP and RAG modes): [`docs/agent-logs/student-3/`](../docs/agent-logs/student-3/)
- MCP server terminal validation: [`docs/ai-evidence/mcp-server/terminal-validation.txt`](../docs/ai-evidence/mcp-server/terminal-validation.txt)
- RAG server terminal validation: [`docs/ai-evidence/rag-server/terminal-validation.txt`](../docs/ai-evidence/rag-server/terminal-validation.txt)
- Pre-commit security scans: [`docs/ai-evidence/student-3/pre-commit-security-scan.txt`](../docs/ai-evidence/student-3/pre-commit-security-scan.txt)
  and the team hooks on Student 3 files: [`docs/ai-evidence/student-3/pre-commit-group-hooks.txt`](../docs/ai-evidence/student-3/pre-commit-group-hooks.txt)
- Post-commit endpoint tests: [`docs/ai-evidence/student-3/post-commit-endpoint-tests.txt`](../docs/ai-evidence/student-3/post-commit-endpoint-tests.txt);
  CI uploads the JUnit report as the `student-3-junit` artefact for the Testing Review mode
- Multi-Agent workflow audit (Planner → Worker → Reviewer → human review): [`reports/multi-agent/`](../reports/multi-agent/) (task `Pharmacy reorder decision: …`)
- Multi-Agent Workflow Review of that workflow (9/9 checks): [`docs/agent-logs/student-3/`](../docs/agent-logs/student-3/) (`*-multi-agent-review.*`)

## Known limitations

- The Multi-Agent Server accepts a review at once and runs the agents in the background; the
  dashboard re-reads the workflow every 3 seconds and shows each agent as it finishes (a few
  minutes on a laptop). In `auto` mode an agent whose live answer fails the server's checks is
  replaced by a rule-based fallback, labelled "Rule-based fallback" on the card.
  `deepseek-r1:8b` as Reviewer was too slow and returned invalid JSON on a laptop, so the
  demo uses `llama3.1:8b`.
- Approving a multi-agent review records the decision only; the order is placed from the
  Purchase Orders page.
- Demonstration roles are not authentication; anyone reaching the frontend can pick any role.
- Compose signs the demo session cookie with a fixed development key unless
  `STUDENT3_FLASK_SECRET_KEY` is set.
- MCP stock alerts list at most 10 rows per category; the counts show the full totals.
- RAG answers come from documentation, not live data; live stock figures come from MCP.
- The first RAG answer after start-up takes about 20 seconds while the local model loads.
