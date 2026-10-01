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
| **Shared MCP tools** — live stock alerts and a connectivity check | "Shared MCP tools" panel | `GET /api/mcp/status`, `POST /api/mcp/call` | MCP server (8000): `homs_pharmacy_stock_alerts`, `homs_echo` |
| **Pharmacy assistant** — grounded answers with citations and a confidence category, or an insufficient-context response | "Ask the pharmacy assistant" panel | `GET /api/rag/status`, `POST /api/rag/ask` | RAG server (8100), `feature: student-3` |

- Boundaries: the backend exposes only the two pharmacy MCP tools (others return
  403) and asks RAG only about `student-3` and shared knowledge; citations from any
  other feature are rejected. Both tools are read-only and never change inventory.
- Configuration: `MCP_ENABLED` / `RAG_ENABLED` (default `false`), `MCP_SERVER_URL`,
  `RAG_SERVER_URL`. Docker Compose points them at `host.docker.internal`; AI-Mode also
  uses the host's Ollama. MCP, RAG and Ollama are never containerised.
- Pharmacy knowledge base: [`ai-services/rag-server/knowledge/student-3/`](../ai-services/rag-server/knowledge/student-3/).
- Pharmacy MCP tool: [`ai-services/mcp-server/tools/pharmacy_stock.py`](../ai-services/mcp-server/tools/pharmacy_stock.py).

Student 3 also built the shared RAG server (`ai-services/rag-server/`) and the
agentic loop's RAG validation mode (`ai-services/agentic-loop/rag_loop.py`).

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
python3 ai-services/rag-server/ingest.py
HOMS_RAG_HOST=0.0.0.0 HOMS_RAG_ALLOW_DOCKER_HOST=true python3 ai-services/rag-server/server.py
```

Then start this feature with MCP and RAG enabled:

```bash
STUDENT3_MCP_ENABLED=true STUDENT3_RAG_ENABLED=true docker compose up -d --build student-3-database student-3-backend student-3-frontend
```

Open <http://localhost:3300>, choose a role, and use the two panels at the top.
Without the `STUDENT3_*_ENABLED=true` overrides the panels report "disabled",
which is the CI configuration. To run without Docker, see each service's README.

## Test

| What | Command (repository root) | Expected |
|---|---|---|
| Unit tests | `python3 -m pytest -q student-3/database/tests`, then `backend/tests`, then `frontend/tests` (each separately) | 4, 43 and 36 passing |
| MCP in the UI | "Get stock alerts via MCP"; "Test connectivity" | "Valid tool result" with counts and tables; "Connection OK" |
| RAG in the UI | Ask "Who can write off an expired batch?" | Answer with a confidence badge and a cited source |
| Insufficient context | Ask "What is the capital of France?" | "Not enough information…"; no answer, no sources |
| Agentic loop | `python3 ai-services/agentic-loop/agentic_loop.py --mode mcp --student 3 --question "…"` and `--mode rag --student 3` | Grounded answer / all checks PASS |

CI (`student-3.yml`) runs the unit tests, builds all three images, and checks that
MCP and RAG are present but disabled in both the backend and the dashboard.

## Evidence

- Docker integration: [`docs/ai-evidence/student-3/docker-integration-validation.txt`](../docs/ai-evidence/student-3/docker-integration-validation.txt)
  (captured with `STUDENT3_MCP_ENABLED=true STUDENT3_RAG_ENABLED=true`, the demo switches)
- Database operations: [`docs/ai-evidence/student-3/database-verification.txt`](../docs/ai-evidence/student-3/database-verification.txt)
- Agentic loop runs (MCP and RAG modes): [`docs/agent-logs/student-3/`](../docs/agent-logs/student-3/)
- RAG server terminal validation: [`docs/ai-evidence/rag-server/terminal-validation.txt`](../docs/ai-evidence/rag-server/terminal-validation.txt)

## Known limitations

- Demonstration roles are not authentication; anyone reaching the frontend can pick any role.
- MCP stock alerts list at most 10 rows per category; the counts show the full totals.
- RAG answers come from documentation, not live data; live stock figures come from MCP.
- The first RAG answer after start-up takes about 20 seconds while the local model loads.
