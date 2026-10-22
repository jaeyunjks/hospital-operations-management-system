# Release 2 — Multi-Agent + Deployment

Release 2 implementation and deployment documentation.

## Agreed high-level scope

- **Multi-Agent System** — `ai-services/multi-agent-server/`.
- **Testing** across the integrated application.
- **Azure** cloud deployment (preferred service: **Azure Container Apps**).

## Shared implementation

- Multi-Agent roles, coordination, Human Review and audit history are under
  `ai-services/multi-agent-server/`.
- Integrated validation remains in `.github/workflows/integration-ci.yml`.
- The controlled shared Azure foundation, OIDC and incremental deployment
  contract are documented in [`azure-deployment.md`](azure-deployment.md).

Do not invent implementation details that have not been agreed.
