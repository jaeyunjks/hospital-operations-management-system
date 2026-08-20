# GitHub Actions Workflows

This directory holds the **individual CI/CD workflow per student** required by
the ASD 2026 project (one owner, one workflow):

- `student-1.yml`
- `student-2.yml`
- `student-3.yml`
- `student-4.yml`
- `student-5.yml`

plus two team-wide workflow placeholders:

- `integration-ci.yml` — integration CI for the assembled application.
- `cloud-deployment.yml` — deployment to Microsoft Azure (Azure Container Apps).

## Current status: placeholders

Each `student-N.yml` is currently a **placeholder**, not a working CI pipeline.
There is no application code to lint, build, or test yet, so implementing full
pipelines now would only produce **fake passing checks** — which this scaffold
deliberately avoids.

To make that honest, each placeholder workflow:

- triggers on **`workflow_dispatch` only** (manual run), so it does **not** run
  automatically on push / pull request and cannot report a green tick against
  untested code;
- performs no build/test — it prints a "not yet implemented" notice.

## What each workflow must eventually do

When a student's microservice exists, replace the placeholder body with a real
pipeline scoped to that student's `student-N/` path, typically:

1. Trigger on `push` / `pull_request` filtered to `paths: student-N/**`.
2. Set up Python 3.x.
3. Install dependencies (`student-N/requirements.txt`).
4. Lint.
5. Run tests (`pytest student-N/tests`).
6. Build the Docker image from `student-N/Dockerfile`.
7. (Later releases) publish the image and/or deploy to Azure Container Apps.

The team-wide placeholders follow the same honesty rule (`workflow_dispatch`
only, no build/test/deploy):

- `integration-ci.yml` — will eventually build and test the assembled stack via
  `docker-compose.yml` (all five microservices + shared frontend + AI services).
- `cloud-deployment.yml` — will eventually deploy to Microsoft Azure (Azure
  Container Apps), using repository secrets for credentials (never committed).
