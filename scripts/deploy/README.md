# Cloud deployment: Azure Container Apps

One shared Azure environment for the whole team. Each student can deploy
their own slice (database, backend, frontend) whenever it is ready.

```text
Azure resource group homs-r2-rg (australiaeast)
├── Container Registry homsr2<id>     images: homs-<app>:<tag>
└── Container Apps environment homs-r2-env
    ├── s1-db  s2-db  s3-db  s4-db  s5-db      internal only (http://sN-db)
    ├── s1-api s2-api s3-api s4-api s5-api     public HTTPS
    ├── s1-web s2-web s3-web s4-web s5-web     public HTTPS
    └── homs-home                              public HTTPS
```

AI-Mode, MCP, RAG and the Multi-Agent Server are **disabled by default** in
every cloud service (Release 2 cloud baseline). Every service setting lives
in the `SERVICES` table in `azure_deploy.py`.

## 0. Requirements (once per computer)

- Azure CLI: `winget install Microsoft.AzureCLI` (Windows) or https://aka.ms/installazurecli
- Docker Desktop running (images are built locally and pushed)
- Python 3.10+

```bash
az login
az account show --query "{name:name, id:id}"
```

## 1. Shared environment (once, by the subscription owner)

```bash
python scripts/deploy/azure_deploy.py setup
```

Creates the resource group, the container registry and the Container Apps
environment. If the region is blocked by the student subscription's policy,
pick another one and rerun, e.g. PowerShell `$env:HOMS_AZURE_LOCATION="southeastasia"`.

Give a teammate access to deploy (they must be in the UTS directory):

```bash
az role assignment create --assignee <their UTS email> --role Contributor \
  --scope /subscriptions/<subscription id>/resourceGroups/homs-r2-rg
```

## 2. Deploy

```bash
python scripts/deploy/azure_deploy.py deploy --only student-4   # your slice
python scripts/deploy/azure_deploy.py deploy                    # everything
python scripts/deploy/azure_deploy.py status                    # public URLs
```

- `--always-on` keeps one replica of every app running (use it on recording
  day; otherwise backends and frontends scale to zero and the first request
  after idle takes 10 to 30 seconds).
- `--build acr` builds in Azure instead of local Docker (not available on
  every student subscription), `--build skip --tag <tag>` reuses pushed images.
- A backend calls its database as `http://sN-db` and a frontend calls its
  backend as `http://sN-api`, inside the environment.

## 3. Verify and keep evidence

```bash
python scripts/deploy/azure_deploy.py smoke     # docs/test-evidence/cloud/<time>-smoke.json
python scripts/deploy/azure_deploy.py probes    # docs/test-evidence/cloud/cloud-probes.json
python ai-services/agentic-loop/agentic_loop.py --mode cloud \
  --probes docs/test-evidence/cloud/cloud-probes.json --allow-writes \
  --decision release --reviewer "<name>" --reason "<why>"
```

The last command is the Release 2 Cloud Deployment Review mode: it probes
the deployed app (CRUD, AI services disabled) and records the final human
release decision in `docs/agent-logs/group/`.

## 4. GitHub Actions (cloud-deployment.yml)

`validate` (every student's tests + this script's tests) must pass before
`deploy` builds, pushes and rolls out the images and `verify` checks the
public apps. Run it from **Actions → cloud-deployment → Run workflow**
(optionally `only: student-4`), or by pushing to `main`.

It needs one repository secret, `AZURE_CREDENTIALS`:

```bash
az ad sp create-for-rbac --name homs-r2-github --role contributor \
  --scopes /subscriptions/<subscription id>/resourceGroups/homs-r2-rg --json-auth
```

Paste the whole JSON output into **Settings → Secrets and variables →
Actions → New repository secret** named `AZURE_CREDENTIALS`. If the UTS
directory does not allow creating a service principal, deploy with the
script locally (step 2) and record that limitation in the report.

## 5. Cost and clean-up

Consumption plan, 0.25 vCPU and 0.5 GiB per app. Databases always keep one
replica; the rest scale to zero unless `--always-on`. After marking:

```bash
python scripts/deploy/azure_deploy.py teardown --yes
```

Databases are SQLite inside each container, seeded on start, so cloud data
resets when a database app restarts. That is acceptable for the showcase.

## Test

```bash
python -m pytest -q scripts/deploy/tests
```

Offline: a fake Azure CLI records the commands, so no account is needed.
