# Release 2 shared Azure deployment

This is the deployment contract for the group HOMS environment. It is designed
for controlled, incremental deployment: the team creates the shared foundation
once, then each student deploys only their own frontend, backend and database.

The shell scripts under `scripts/deploy/` and the OIDC-based
`cloud-deployment.yml` are the single authoritative implementation. The older
`azure_deploy.py` prototype is retained only for historical evidence and
offline regression tests; it must not be used for Release 2 deployment.

## Architecture

One Azure subscription contains:

- one resource group (`homs-r2-rg` by default);
- one Azure Container Registry;
- one Log Analytics workspace;
- one Azure Container Apps environment;
- one user-assigned identity with `AcrPull` on the registry;
- one storage account with up to five dedicated Azure Files shares;
- three uniquely named Container Apps per student.

Only frontends have external ingress. Backends and databases have internal
ingress and use the FQDN Azure assigns after deployment; Docker Compose service
names and `host.docker.internal` are never used in the cloud.

The shared environment is an administrative and network boundary, not a shared
application resource name. A Student 5 deployment updates only:

```text
s5-web
s5-api
s5-db
```

It does not enumerate, replace or delete another student's apps.

## Naming contract

| Resource | Default/pattern |
|---|---|
| Resource group | `homs-r2-rg` |
| Container Apps environment | `homs-r2-env` |
| Log Analytics workspace | `homs-r2-logs` |
| ACR pull identity | `homs-r2-acr-pull` |
| Container App, Students 1–4 | `homs-r2-student<N>-<frontend|backend|database>` |
| Container App, Student 5 | `s5-web`, `s5-api`, `s5-db` |
| ACR repository | `student<N>/<frontend|backend|database>` |
| Image tag | full Git commit SHA; never `latest` |
| Azure Files share | `student<N>-db` |
| Environment storage link | `student<N>-db-storage` |

ACR and storage-account names are globally unique Azure DNS names. They have no
safe universal default and must be supplied through `HOMS_AZURE_ACR_NAME` and
`HOMS_AZURE_STORAGE_ACCOUNT`. Both accept only lowercase letters and numbers.

Shared resource names and the Student 1–4 app prefix can be configured through
the environment variables documented by
`./scripts/deploy/azure-foundation.sh --help`. Student 5 app names are fixed to
the existing `s5-*` convention for shared-tool compatibility. All team members
must use the same shared values after the foundation is created.

## Database persistence

Each database app mounts only its own Azure Files share at `/data` and runs
with exactly one minimum and maximum replica. The application-specific path is:

| Student | Persistent database path |
|---|---|
| 1 | `/data/patients.db` |
| 2 | `/data/clinical_staff.db` |
| 3 | `/data/pharmacy.db` |
| 4 | `/data/rooms.db` |
| 5 | `/data/staff_shift.db` |

Students 1 and 4 retain their existing seeded-image behavior for builds, while
their runtime container configuration now supports an empty persistent `/data`
mount. No database share or SQLite file is shared between students.

SQLite remains a single-writer demonstration deployment. Do not increase a
database app above one replica. A production-scale deployment would require a
managed database rather than shared-file SQLite.

## Cloud AI baseline

Every deployed backend receives these values:

```text
AI_ENABLED=false
MCP_ENABLED=false
RAG_ENABLED=false
MULTI_AGENT_ENABLED=false
```

Student 3 also receives `AGENT_ENABLED=false`. Students 1, 2 and 4 now stop at
their Ollama client boundary when AI is disabled. Student 5 rejects Staffing
Recovery start, polling and Human Review requests before constructing or using
a Multi-Agent network client. No Ollama, MCP, RAG or Multi-Agent service is
deployed by the baseline workflow.

The enabled local Release 2 demonstration remains unchanged: Student 5's
Multi-Agent switch defaults to enabled locally and the cloud deployment sets it
false explicitly.

## One-time cloud-owner setup

Prerequisites:

- Azure CLI authenticated with `az login`;
- permission to create resources and role assignments in the subscription;
- required Azure resource providers already registered by the subscription owner;
- permission to create an Entra application and federated credential;
- GitHub CLI authenticated with repository-administration access;
- two available, globally unique names for ACR and the storage account.

Set the common values in the same terminal for both setup steps:

```bash
export AZURE_SUBSCRIPTION_ID='<subscription-id>'
export HOMS_AZURE_ACR_NAME='<globally-unique-lowercase-name>'
export HOMS_AZURE_STORAGE_ACCOUNT='<globally-unique-lowercase-name>'
export GITHUB_REPOSITORY='jaeyunjks/hospital-operations-management-system'
```

Preview without changing Azure:

```bash
./scripts/deploy/azure-foundation.sh --student 5 --dry-run
./scripts/deploy/github-oidc-setup.sh --dry-run
```

Provision the foundation, then establish OIDC:

```bash
./scripts/deploy/azure-foundation.sh --student 5
./scripts/deploy/github-oidc-setup.sh
```

The foundation script does not call `az provider register`; subscription-level
provider registration is outside this deployment path. With `--student 5`, it
creates or reuses only the `student5-db` Azure Files share and
`student5-db-storage` Container Apps environment link. Shared resources such as
the resource group, registry, environment and pull identity remain reusable by
the group. Omitting `--student` retains the team-wide all-student foundation
behavior. When `homs-r2-env` already exists, the script reuses it without
creating an unused Log Analytics workspace or changing its logging setup.

The foundation script reuses an existing ACR without changing its current
admin-account setting. After a managed-identity deployment and image pull have
been proven, the cloud owner may separately disable an existing ACR admin
account. That hardening action is intentionally not part of foundation setup.

The OIDC script creates no client password. It creates/reuses the `azure-r2`
GitHub environment and configures these repository secret names:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

It also records the non-secret resource names as repository variables. The
federated subject is:

```text
repo:<owner>/<repository>:environment:azure-r2
```

The deployment identity receives `Contributor` only at the shared resource
group and `AcrPush` only at the shared registry. Creating role assignments may
require Owner or User Access Administrator. Creating the Entra application may
require an Azure tenant administrator, depending on tenant policy. Creating the
GitHub environment, secrets and variables requires repository administration.

## Per-student deployment

The controlled path is GitHub Actions:

1. Confirm the student's normal CI workflow is green.
2. Open **Actions → cloud-deployment → Run workflow**.
3. Choose exactly one student number.
4. Review the validation job before the OIDC deployment job runs.
5. Download the non-secret deployment evidence artifact.

GitHub only exposes a manually dispatched workflow after that workflow exists
on the repository's default branch. The team must therefore review this change
on `develop`, then merge it through the normal pull-request process to `main`
before treating **Run workflow** as operational. Do not change the repository
default branch merely to bypass that review step.

The equivalent authenticated command for Student 5 is:

```bash
./scripts/deploy/deploy-student.sh 5
./scripts/deploy/deployment-testing.sh 5
```

The deployment script builds each image remotely in ACR, tags it with the
current Git SHA, upserts only the selected student's three apps, and records a
non-secret receipt under `.local-r2/cloud/`.

For Student 5, those apps are `s5-db`, `s5-api`, and `s5-web`. The database and
backend have internal ingress, while the frontend is external. Runtime wiring
uses the Azure-assigned internal FQDNs: `s5-api` receives
`DATABASE_SERVICE_URL=https://<s5-db-fqdn>` and `s5-web` receives
`STAFF_SHIFT_API_URL=https://<s5-api-fqdn>`. The database receives
`STUDENT5_DATABASE_PATH=/data/staff_shift.db`, mounts only
`student5-db-storage` at `/data`, and remains fixed at one replica.

An `all` deployment scope is intentionally not offered until every slice has
completed an individual cloud validation. This prevents one unvalidated slice
or broad update from affecting the other students.

## Validation and evidence

`deployment-testing.sh` verifies:

- the public frontend;
- backend health from within the frontend container;
- database health from within the backend container;
- one representative read-only API operation;
- explicit AI/MCP/RAG/Multi-Agent false values;
- the `/data` Azure Files mount and one-replica database scale;
- for Student 5, that Staffing Recovery returns the disabled response without
  attempting the local Multi-Agent server.

It sends no create, update or delete application request.

## Deprecated prototype

`scripts/deploy/azure_deploy.py` is not called by the active workflow. It uses
a different resource naming, registry authentication and ephemeral database
contract and remains only to preserve historical cloud evidence and its
offline tests. `AZURE_CREDENTIALS`, ACR admin credentials, `/tmp` SQLite paths,
and its deploy-all behavior are not part of the supported deployment path.

## Cleanup warning

No cleanup script is automated because deletion affects shared team resources.
Individual apps can be deleted only after resolving their exact names:

```bash
az containerapp list --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --output table
az containerapp delete --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --name '<exact-student-app-name>'
```

The following command is intentionally **not** wrapped in a script. It deletes
the entire shared foundation, all five students' apps, storage and data:

```bash
az group delete --name "$HOMS_AZURE_RESOURCE_GROUP"
```

Only the team cloud owner should run cleanup after explicit whole-team approval
and any required data export.

## Reference documentation

- [Azure Container Apps ingress](https://learn.microsoft.com/azure/container-apps/ingress)
- [Azure Files mounts for Container Apps](https://learn.microsoft.com/azure/container-apps/storage-mounts)
- [GitHub Actions authentication using Azure OIDC](https://learn.microsoft.com/azure/developer/github/connect-from-azure-openid-connect)

