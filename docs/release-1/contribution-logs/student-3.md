# Release 1 Contribution Log — Student 3

| | |
|---|---|
| Student | Tirth Patel (GitHub `tirth676`) |
| Feature | Pharmacy & Medication Inventory Management (`student-3/`) |
| Feature branch | `pharmacy-inventory-management`, merged into `develop` |
| Release 1 period | 23 September – 1 October 2026 |
| Release 1 commits | Listed below by requirement (plus one Release 0 carry-over); merged into `develop` on 24, 28 and 30 September and 1 October |

Commit links: `https://github.com/jaeyunjks/hospital-operations-management-system/commit/<id>`.

## Summary

| Area | Responsibility | Outcome |
|---|---|---|
| Individual feature | MCP and RAG access from the pharmacy frontend through its backend | Dashboard panels for live stock and purchase-order alerts (MCP) and grounded pharmacy answers (RAG); backend `/api/mcp/*` and `/api/rag/*` |
| Individual workflow | `student-3.yml` | Unit tests, image builds, and checks that MCP and RAG are retained but disabled in CI |
| Shared MCP server | Pharmacy tools | `homs_pharmacy_stock_alerts` and `homs_pharmacy_order_alerts`, read-only allowlisted tools over the Student 3 API, plus terminal validation |
| Shared RAG server | Whole component | Server, retrieval, relevance gate, cited answers, confidence categories, insufficient-context handling, CLI, tests, API contract |
| Shared agentic loop | RAG validation mode | `--mode command|mcp|rag` switch and `rag_loop.py` |
| Integration | Docker Compose and `develop` | Student 3 backend reaches host MCP, RAG and Ollama; validated in the full Compose deployment |

## Commits

### Individual feature: MCP access

| Date | Commit | Change |
|---|---|---|
| 23 Sep | [`da1ca26`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/da1ca26) | Backend `/api/mcp/status` and `/api/mcp/call`: pharmacy tool allowlist, `MCP_ENABLED` flag, 502/503/504 mapping, tests |
| 23 Sep | [`888e5f4`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/888e5f4) | Dashboard "Shared MCP tools" panel with structured tool results |
| 23 Sep | [`2db28fc`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/2db28fc) | Compose: backend reaches the host MCP server via `host.docker.internal` |
| 23 Sep | [`8f0d165`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/8f0d165) | CI: unit tests and MCP-disabled checks for backend and dashboard |

### Individual feature: RAG access

| Date | Commit | Change |
|---|---|---|
| 28 Sep | [`942feec`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/942feec) | Backend `/api/rag/status` and `/api/rag/ask`: pharmacy-scoped questions, response contract validation, `RAG_ENABLED` flag, tests |
| 28 Sep | [`8756272`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/8756272) | Dashboard "Ask the pharmacy assistant" panel: answer, citations, confidence badge, insufficient-context state |
| 28 Sep | [`8791ccd`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/8791ccd) | Compose: backend reaches the host RAG server |
| 28 Sep | [`99303d3`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/99303d3) | CI: RAG-disabled checks for backend and dashboard |

### Individual feature: integration, usability and documentation

| Date | Commit | Change |
|---|---|---|
| 28 Sep | [`2b72900`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/2b72900) | Compose: Student 3 AI-Mode uses the host's non-containerised Ollama |
| 28 Sep | [`ab93c3e`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/ab93c3e) | MCP and RAG panels wrap correctly on narrow screens |
| 30 Sep | [`4a44da7`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/4a44da7) | MCP and RAG panels moved to the top of the dashboard |
| 30 Sep | [`b3c36d6`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/b3c36d6) | Explicit "Connection OK" result for the MCP connectivity check |
| 30 Sep | [`c8a9122`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/c8a9122) | Feature README replaced with the Release 0 and 1 overview; ownership recorded |
| 30 Sep | [`f8de714`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/f8de714) | Compose: Student 3 MCP and RAG off by default so integration CI keeps them disabled |
| 30 Sep | [`10dc895`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/10dc895) | Sign-in page no longer says 'Release 0'; README installs the shared servers' dependencies |
| 1 Oct | [`8303b21`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/8303b21) | Dashboard hides technical details (structured JSON, server URLs, raw tool arguments) |
| 1 Oct | [`7a1a99a`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/7a1a99a) | Stock-alert figures are clickable and show only their own details |
| 1 Oct | [`01c12fe`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/01c12fe) | README test counts for the frontend suite |
| 1 Oct | [`4f62c97`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/4f62c97) | 'Expiring soon' opens on batches expiring within 7 days, then 8–30 days |
| 1 Oct | [`1381e29`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/1381e29) | 'Low stock' opens on the low-stock list |
| 1 Oct | [`7f26739`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/7f26739) | Purchase-order alerts on the dashboard (MCP) and purchase-order example questions (RAG); centred sign-in page; RAG answers keep citations and confidence without technical details |

### Shared components

| Date | Commit | Change |
|---|---|---|
| 23 Sep | [`4696cd0`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/4696cd0) | MCP server: `homs_pharmacy_stock_alerts` tool with input validation, consistency checks and an allowlisted projection; 41 tests |
| 24 Sep | [`35457e0`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/35457e0) | RAG knowledge base: shared overview, pharmacy documents, per-student authoring guides |
| 24 Sep | [`3216417`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/3216417) | RAG server: ingestion, retrieval, calibrated relevance gate, citation validation, confidence categories, CLI, 62 tests, API contract |
| 24 Sep | [`fcf2668`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/fcf2668) | Agentic loop: `--mode` switch and RAG validation mode; 19 tests |
| 30 Sep | [`33ec7d8`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/33ec7d8) | RAG: pharmacy documents no longer read as live stock data; pharmacy overview document |
| 1 Oct | [`c790d10`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/c790d10) | MCP server: `homs_pharmacy_order_alerts` read-only tool (38 tests) and terminal validation (`cli.py`, `validate.sh`) |

### Documentation and records

| Date | Commit | Change |
|---|---|---|
| 30 Sep | [`991e13f`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/991e13f) | This contribution log |
| 30 Sep | [`f7627e8`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/f7627e8) | Release 1 index (`docs/release-1/README.md`) and current Student 5 MCP status |
| 30 Sep | [`77b297e`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/77b297e) | Contribution log corrections from the review |
| 1 Oct | [`4de0168`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/4de0168) | Contribution log merge dates |

### Validation evidence

| Date | Commit | Evidence |
|---|---|---|
| 23 Sep | [`e0f124b`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/e0f124b) | Release 0 carry-over: read-only database verifier and Release 0 loop logs |
| 24 Sep | [`dfedbd2`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/dfedbd2) | RAG server terminal validation |
| 24 Sep | [`8fd7d40`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/8fd7d40) | Agentic loop in MCP and RAG modes (local) |
| 28 Sep | [`b03eae9`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/b03eae9) | Docker Compose integration validation and loop reruns against the Compose deployment |
| 30 Sep | [`219022d`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/219022d) | Database operations: `verify_db.py` on the live Compose data (41/41) and database-service reads |
| 1 Oct | [`b932624`](https://github.com/jaeyunjks/hospital-operations-management-system/commit/b932624) | Order-alerts tool in the Docker evidence; MCP-mode loop run using both pharmacy tools (grounded) |

## Integration and validation activities

- Merged `develop` into the feature branch before each integration and merged the feature
  branch into `develop` on 24, 28 and 30 September and 1 October, each time after CI passed.
- Validated the full Compose deployment (15 feature containers plus the shared home page,
  with Ollama outside Compose): the Student 3 backend container reached the host MCP server,
  RAG server and Ollama.
- Verified the Student 3 database (structure, data and relationships: 41/41 checks) on a copy
  of the live Compose volume, and recorded reads through the database service.
- Ran the shared agentic loop for Student 3 in MCP mode (grounded) and RAG mode (all checks
  passed, including an ADAPT retry of a failed question).
- CI runs: [student-3.yml](https://github.com/jaeyunjks/hospital-operations-management-system/actions/workflows/student-3.yml),
  [integration-ci](https://github.com/jaeyunjks/hospital-operations-management-system/actions/workflows/integration-ci.yml).

| Evidence | Location |
|---|---|
| Docker integration | `docs/ai-evidence/student-3/docker-integration-validation.txt` |
| Database operations | `docs/ai-evidence/student-3/database-verification.txt` |
| RAG terminal validation | `docs/ai-evidence/rag-server/terminal-validation.txt` |
| Agentic loop outputs | `docs/agent-logs/student-3/*-grounded-loop.*`, `*-rag-validation.*` |

## AI assistance declaration

> **Draft — confirm or edit before submitting, and check it against the unit's AI-use policy.**

Development of this contribution was assisted by Claude Code (Anthropic's AI coding
assistant), which drafted code, tests, documentation and evidence under my direction.
Commits it co-wrote carry a `Co-Authored-By: Claude` trailer. I chose the scope and
design decisions, reviewed the changes, and ran the application, tests and validations
recorded above.
