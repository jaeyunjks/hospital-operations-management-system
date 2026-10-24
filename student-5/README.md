# Student 5 — Staff & Shift Management

Student 5 is the HOMS feature slice for hospital staff, availability, shifts,
assignments, coverage, and AI-assisted staffing decisions. It contains separate
HTMX frontend, Flask backend/API, and SQLite database microservices integrated
through the root Docker Compose application.

Feature owner: **Yafie Farabi**

## Release 2 status

The feature implements:

- staff, availability, shift, and assignment management;
- shift CRUD and roster assignment operations;
- coverage summaries and AI-assisted staff suggestions;
- Staffing Recovery from the normal Shift Planner manager workflow;
- RAG, MCP, and shared Multi-Agent integration for local Release 2 use;
- manager-only workflow retrieval and Human Review;
- Student 5 post-commit endpoint testing; and
- an Azure Container Apps deployment for the frontend, backend, and persistent
  database service.

**Student 5 Azure frontend:**
[https://s5-web.purplepebble-f0501bb8.australiaeast.azurecontainerapps.io/](https://s5-web.purplepebble-f0501bb8.australiaeast.azurecontainerapps.io/)

## Staffing Recovery workflow

Staffing Recovery is broader than the single-step staff suggestion function. A
Staff Manager selects a shift and starts this advisory workflow:

```text
Selected shift
  → authoritative backend recovery evidence
  → advisory RAG policy context
  → controlled read-only MCP context
  → shared Multi-Agent Server
  → Planner → Worker → Reviewer → Human Review
  → final advisory result and persistent audit
```

The Student 5 backend remains authoritative for shift state, coverage,
eligibility, availability, and schedule conflicts. RAG supplies labelled policy
guidance and source provenance; it cannot change deterministic eligibility.
MCP supplies labelled read-only operational context through an allow-listed
tool interface.

The shared Multi-Agent Server calls the configured local Ollama model for each
role. Model output is schema- and semantically validated. In automatic mode, a
failed model response may use the explicitly labelled deterministic fallback;
the model attempt, validation result, and fallback provenance remain in the
workflow audit.

Human Review accepts or rejects the recommendation only. Starting, polling, or
reviewing a workflow never creates, updates, or removes a roster assignment.
Any operational assignment remains a separate manager-controlled action.

Implementation entry points include:

- [`backend/routes/recovery_routes.py`](backend/routes/recovery_routes.py)
- [`backend/routes/recovery_workflow_routes.py`](backend/routes/recovery_workflow_routes.py)
- [`backend/services/recovery_service.py`](backend/services/recovery_service.py)
- [`backend/services/recovery_policy_service.py`](backend/services/recovery_policy_service.py)
- [`backend/services/recovery_mcp_service.py`](backend/services/recovery_mcp_service.py)
- [`backend/services/multi_agent_client.py`](backend/services/multi_agent_client.py)
- [`frontend/templates/recovery_workflow.html`](frontend/templates/recovery_workflow.html)

Shared workflow audits are stored under
[`../reports/multi-agent/`](../reports/multi-agent/). One complete Student 5
example is
[`workflow-d44bd416-2a97-4e51-b81e-fdd53f70e75c.json`](../reports/multi-agent/workflow-d44bd416-2a97-4e51-b81e-fdd53f70e75c.json).

## Architecture and local operation

```text
HTMX frontend :3500
  → Flask backend/API :5500
      → SQLite database service :6500
      → host-local Ollama / RAG / MCP / Multi-Agent services
```

The frontend communicates with the Student 5 backend; it does not call the
database or shared AI services directly. The backend calls the database service
rather than opening the SQLite file itself.

From the repository root, start the complete local Student 5 Release 2 stack
with:

```bash
./scripts/start-student5-r2.sh
```

Stop it with:

```bash
./scripts/stop-student5-r2.sh
```

## Testing and CI/CD

Student 5 tests cover CRUD, authorization, service integration, recovery
evidence, RAG/MCP enrichment, asynchronous Multi-Agent lifecycle, Human Review,
failure handling, and mutation safety.

- Student workflow: [`../.github/workflows/student-5.yml`](../.github/workflows/student-5.yml)
- Cloud workflow: [`../.github/workflows/cloud-deployment.yml`](../.github/workflows/cloud-deployment.yml)
- Deployment script: [`../scripts/deploy/deploy-student.sh`](../scripts/deploy/deploy-student.sh)
- Deployment validation: [`../scripts/deploy/deployment-testing.sh`](../scripts/deploy/deployment-testing.sh)
- Azure deployment contract: [`../docs/release-2/azure-deployment.md`](../docs/release-2/azure-deployment.md)

Run the Student 5 suite from the repository root:

```bash
python3 -m pytest -q student-5/tests
```

## Azure baseline

The deployed Student 5 slice uses `s5-web`, `s5-api`, and `s5-db`. The frontend
has external ingress; the backend and database use internal ingress. The SQLite
database persists on the dedicated `student5-db` Azure Files share at
`/data/staff_shift.db`, with one database replica.

Release 2 deploys a deliberately non-AI cloud baseline:

```text
AI_ENABLED=false
MCP_ENABLED=false
RAG_ENABLED=false
MULTI_AGENT_ENABLED=false
```

These settings do not remove local Release 2 functionality. They prevent the
cloud backend from depending on host-local AI services and do not claim the
optional cloud AI bonuses.
