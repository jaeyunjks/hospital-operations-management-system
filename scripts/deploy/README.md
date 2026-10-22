# Azure deployment helpers

The authoritative Release 2 deployment path is the shell/OIDC workflow:

```text
azure-foundation.sh
        ↓
github-oidc-setup.sh (read-only verification)
        ↓
deploy-student.sh <1-5>
        ↓
deployment-testing.sh <1-5>
```

This path provides one shared Azure Container Apps environment with uniquely
named student services, managed-identity ACR pulls, dedicated Azure Files
shares for SQLite, explicit cloud-off AI flags, immutable Git SHA image tags,
and non-destructive post-deployment validation.

## Preview safely

Set the common non-secret resource names and preview every step:

```bash
export AZURE_SUBSCRIPTION_ID='<subscription-id>'
export HOMS_AZURE_ACR_NAME='<existing-acr-name>'
export HOMS_AZURE_STORAGE_ACCOUNT='<globally-unique-storage-name>'
export GITHUB_REPOSITORY='jaeyunjks/hospital-operations-management-system'

./scripts/deploy/azure-foundation.sh --student 5 --dry-run
./scripts/deploy/github-oidc-setup.sh --dry-run
./scripts/deploy/deploy-student.sh 5 --dry-run
./scripts/deploy/deployment-testing.sh 5 --dry-run
```

When authenticated to the configured Azure subscription, the foundation dry
run uses read-only queries to label resources as `exists` or `missing`. It
never creates or changes resources.

Azure resource providers are assumed to have been registered by the
subscription owner. The foundation script never calls `az provider register`.
For a Student 5-only rollout, `--student 5` limits share/storage-link inspection
and provisioning to `student5-db` / `student5-db-storage`.

## One-time cloud-owner setup

After reviewing the dry run:

```bash
./scripts/deploy/azure-foundation.sh --student 5
./scripts/deploy/github-oidc-setup.sh
```

The first script creates or reuses the resource group, ACR, Container Apps
environment, Log Analytics workspace, managed ACR pull identity, storage
account, and the selected student's isolated database share/storage link. Omit
`--student` only when the cloud owner intentionally wants all five isolated
share/storage links. The second command is now read-only: it verifies the
pre-created `homs-r2-github` user-assigned managed identity, the `develop` and
`main` branch federated credentials, Contributor access on `homs-r2-rg`, and
the three repository secret names. It never creates an Entra application,
service principal, identity, credential, role assignment, or GitHub setting.

The workflow uses repository secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
`AZURE_SUBSCRIPTION_ID` with `azure/login@v2`. It deliberately has no GitHub
environment binding because the existing federated subjects are branch-based.
No client secret or `AZURE_CREDENTIALS` JSON is used.

## Deploy one student

```bash
./scripts/deploy/deploy-student.sh 5
./scripts/deploy/deployment-testing.sh 5
```

Student 5 uses the existing shared `homs-r2-env` and the established app names
`s5-db`, `s5-api`, and `s5-web`. Its image repositories remain
`student5/database`, `student5/backend`, and `student5/frontend`. Student 1–4
naming remains unchanged.

The active GitHub workflow performs the same operations after requiring an
explicit Student 1–5 selection. There is no deploy-all option.

## Legacy prototype

`azure_deploy.py` is a deprecated historical prototype retained only so its
existing evidence and offline regression tests remain reproducible. It is not
called by `cloud-deployment.yml` and must not be used for Release 2 deployment.
It uses a different naming/authentication/persistence contract and is not a
supported alternative to the shell/OIDC path.

Full architecture, configuration, permissions, default-branch requirements,
and cleanup warnings are in
[`docs/release-2/azure-deployment.md`](../../docs/release-2/azure-deployment.md).
