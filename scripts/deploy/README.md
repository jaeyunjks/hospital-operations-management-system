# Azure deployment helpers

The authoritative Release 2 deployment path is the shell/OIDC workflow:

```text
azure-foundation.sh
        ↓
github-oidc-setup.sh
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

./scripts/deploy/azure-foundation.sh --dry-run
./scripts/deploy/github-oidc-setup.sh --dry-run
./scripts/deploy/deploy-student.sh 5 --dry-run
./scripts/deploy/deployment-testing.sh 5 --dry-run
```

When authenticated to the configured Azure subscription, the foundation dry
run uses read-only queries to label resources as `exists` or `missing`. It
never creates or changes resources.

## One-time cloud-owner setup

After reviewing the dry run:

```bash
./scripts/deploy/azure-foundation.sh
./scripts/deploy/github-oidc-setup.sh
```

The first script creates or reuses the resource group, ACR, Container Apps
environment, Log Analytics workspace, managed ACR pull identity, storage
account, and five isolated database shares/storage links. The second creates
the GitHub OIDC trust and configures the required GitHub environment, secret
names, and non-secret variables without creating a client password.

## Deploy one student

```bash
./scripts/deploy/deploy-student.sh 5
./scripts/deploy/deployment-testing.sh 5
```

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
