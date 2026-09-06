# GitHub Actions Workflows

This directory holds the **individual CI/CD workflow per student** required by
the ASD 2026 project (one owner, one workflow):

- `student-1.yml`
- `student-2.yml`
- `student-3.yml`
- `student-4.yml`
- `student-5.yml`

plus two team-wide workflows:

- `integration-ci.yml` — integration CI for the assembled application.
- `cloud-deployment.yml` — deployment to Microsoft Azure (Azure Container Apps).

## Current status

`student-1.yml` through `student-5.yml` validate their respective feature's
microservices through scoped test, build, and smoke-check workflows.

`integration-ci.yml` validates the assembled group application: the shared
frontend, all five feature service chains, root Docker Compose, and shared AI
configuration.

`cloud-deployment.yml` is reserved for Microsoft Azure deployment using
repository secrets for credentials.
