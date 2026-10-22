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
- `cloud-deployment.yml` — controlled, manual deployment of one student slice
  to the shared Microsoft Azure Container Apps environment.

## Current status

`student-1.yml` through `student-5.yml` validate their respective feature's
microservices through scoped test, build, and smoke-check workflows.

`integration-ci.yml` validates the assembled group application: the shared
frontend, all five feature service chains, root Docker Compose, and shared AI
configuration.

`cloud-deployment.yml` validates the selected student's tests and deployment
definitions, signs in with GitHub OIDC, deploys only that student's three
uniquely named Container Apps, validates the cloud-off AI baseline, and uploads
a non-secret evidence receipt. It currently supports Student 1–5 selections
individually; an `all` option is deliberately withheld until every slice has
passed its own cloud validation.

One-time Azure/OIDC setup and the required secret and variable names are
documented in
[`docs/release-2/azure-deployment.md`](../../docs/release-2/azure-deployment.md).
