#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=azure-common.sh
source "$SCRIPT_DIR/azure-common.sh"

DRY_RUN=false
GITHUB_REPOSITORY_NAME="${GITHUB_REPOSITORY:-}"
HOMS_OIDC_APP_NAME="${HOMS_OIDC_APP_NAME:-homs-r2-github-deployer}"
HOMS_OIDC_CREDENTIAL_NAME="${HOMS_OIDC_CREDENTIAL_NAME:-homs-r2-environment}"

usage() {
  cat <<'EOF'
Create or reuse the Azure Entra application and GitHub OIDC trust.

Run this after azure-foundation.sh:
  AZURE_SUBSCRIPTION_ID=<id> \
  HOMS_AZURE_ACR_NAME=<name> \
  HOMS_AZURE_STORAGE_ACCOUNT=<name> \
  GITHUB_REPOSITORY=owner/repository \
  ./scripts/deploy/github-oidc-setup.sh [--dry-run]

The script creates no client secret. It writes only the three requested IDs to
GitHub Actions secrets and non-secret resource names to Actions variables.

Options:
  --repository owner/repository
  --dry-run
  --help
EOF
}

while (($#)); do
  case "$1" in
    --repository)
      (($# >= 2)) || die "--repository requires owner/repository."
      GITHUB_REPOSITORY_NAME="$2"
      shift
      ;;
    --dry-run) DRY_RUN=true ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
  shift
done

require_value AZURE_SUBSCRIPTION_ID
require_value HOMS_AZURE_ACR_NAME
require_value HOMS_AZURE_STORAGE_ACCOUNT
validate_acr_name "$HOMS_AZURE_ACR_NAME"
validate_storage_name "$HOMS_AZURE_STORAGE_ACCOUNT"
[[ "$GITHUB_REPOSITORY_NAME" =~ ^[^/]+/[^/]+$ ]] || die \
  "GITHUB_REPOSITORY must use owner/repository format."

OIDC_SUBJECT="repo:${GITHUB_REPOSITORY_NAME}:environment:${HOMS_GITHUB_ENVIRONMENT}"

if [[ "$DRY_RUN" == true ]]; then
  log "Dry run: no Azure or GitHub setting will be changed."
  log "Entra application: $HOMS_OIDC_APP_NAME"
  log "Federated credential: $HOMS_OIDC_CREDENTIAL_NAME"
  log "OIDC subject: $OIDC_SUBJECT"
  log "GitHub environment: $HOMS_GITHUB_ENVIRONMENT"
  log "GitHub secret names: AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID"
  log "GitHub variables: AZURE_RESOURCE_GROUP, AZURE_LOCATION, AZURE_CONTAINER_REGISTRY, AZURE_CONTAINERAPPS_ENVIRONMENT, AZURE_STORAGE_ACCOUNT, AZURE_APP_PREFIX, AZURE_ACR_PULL_IDENTITY"
  exit 0
fi

azure_account_check
select_subscription
require_command gh
gh auth status >/dev/null 2>&1 || die "GitHub CLI is not authenticated. Run 'gh auth login' first."

az group show --name "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1 || die \
  "Shared resource group not found. Run azure-foundation.sh first."

APP_IDS="$(az ad app list --display-name "$HOMS_OIDC_APP_NAME" \
  --query "[?displayName=='$HOMS_OIDC_APP_NAME'].appId" --output tsv --only-show-errors)"
APP_COUNT="$(printf '%s\n' "$APP_IDS" | sed '/^$/d' | wc -l | tr -d ' ')"
if [[ "$APP_COUNT" == "0" ]]; then
  log "Creating Entra application $HOMS_OIDC_APP_NAME."
  APP_ID="$(az ad app create --display-name "$HOMS_OIDC_APP_NAME" \
    --sign-in-audience AzureADMyOrg --query appId --output tsv --only-show-errors)"
elif [[ "$APP_COUNT" == "1" ]]; then
  APP_ID="$APP_IDS"
  log "Reusing Entra application $HOMS_OIDC_APP_NAME."
else
  die "Multiple Entra applications use display name '$HOMS_OIDC_APP_NAME'; an Azure admin must resolve the ambiguity."
fi

if ! az ad sp show --id "$APP_ID" --only-show-errors >/dev/null 2>&1; then
  log "Creating the application service principal."
  az ad sp create --id "$APP_ID" --only-show-errors --output none
else
  log "Reusing the application service principal."
fi

FEDERATED_SUBJECT="$(az ad app federated-credential list --id "$APP_ID" \
  --query "[?name=='$HOMS_OIDC_CREDENTIAL_NAME'].subject | [0]" \
  --output tsv --only-show-errors)"
if [[ -z "$FEDERATED_SUBJECT" ]]; then
  FEDERATED_FILE="$(mktemp)"
  trap 'rm -f "$FEDERATED_FILE"' EXIT
  python3 - "$FEDERATED_FILE" "$HOMS_OIDC_CREDENTIAL_NAME" "$OIDC_SUBJECT" <<'PY'
import json
import sys

path, name, subject = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({
        "name": name,
        "issuer": "https://token.actions.githubusercontent.com/",
        "subject": subject,
        "description": "HOMS R2 controlled Azure deployment environment",
        "audiences": ["api://AzureADTokenExchange"],
    }, handle)
PY
  log "Creating GitHub environment federated credential."
  az ad app federated-credential create \
    --id "$APP_ID" \
    --parameters "$FEDERATED_FILE" \
    --only-show-errors --output none
elif [[ "$FEDERATED_SUBJECT" == "$OIDC_SUBJECT" ]]; then
  log "Reusing matching federated credential $HOMS_OIDC_CREDENTIAL_NAME."
else
  die "Federated credential '$HOMS_OIDC_CREDENTIAL_NAME' trusts '$FEDERATED_SUBJECT', not '$OIDC_SUBJECT'. An Azure admin must review it; the script will not overwrite trust configuration."
fi

SP_OBJECT_ID="$(az ad sp show --id "$APP_ID" --query id --output tsv --only-show-errors)"
RESOURCE_GROUP_ID="$(az group show --name "$HOMS_AZURE_RESOURCE_GROUP" \
  --query id --output tsv --only-show-errors)"
ACR_ID="$(az acr show --name "$HOMS_AZURE_ACR_NAME" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query id --output tsv --only-show-errors)"

if [[ "$(az role assignment list --assignee-object-id "$SP_OBJECT_ID" \
  --scope "$RESOURCE_GROUP_ID" --role Contributor \
  --query 'length(@)' --output tsv --only-show-errors)" == "0" ]]; then
  log "Assigning Contributor at the shared HOMS resource-group scope."
  az role assignment create \
    --assignee-object-id "$SP_OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --role Contributor \
    --scope "$RESOURCE_GROUP_ID" \
    --only-show-errors --output none
else
  log "Contributor assignment already exists."
fi

if [[ "$(az role assignment list --assignee-object-id "$SP_OBJECT_ID" \
  --scope "$ACR_ID" --role AcrPush \
  --query 'length(@)' --output tsv --only-show-errors)" == "0" ]]; then
  log "Assigning AcrPush on the shared registry."
  az role assignment create \
    --assignee-object-id "$SP_OBJECT_ID" \
    --assignee-principal-type ServicePrincipal \
    --role AcrPush \
    --scope "$ACR_ID" \
    --only-show-errors --output none
else
  log "AcrPush assignment already exists."
fi

TENANT_ID="$(az account show --query tenantId --output tsv --only-show-errors)"

log "Creating or reusing GitHub environment $HOMS_GITHUB_ENVIRONMENT."
gh api --method PUT \
  "repos/${GITHUB_REPOSITORY_NAME}/environments/${HOMS_GITHUB_ENVIRONMENT}" \
  --silent

log "Setting GitHub Actions OIDC identifiers without displaying values."
printf '%s' "$APP_ID" | gh secret set AZURE_CLIENT_ID --repo "$GITHUB_REPOSITORY_NAME"
printf '%s' "$TENANT_ID" | gh secret set AZURE_TENANT_ID --repo "$GITHUB_REPOSITORY_NAME"
printf '%s' "$AZURE_SUBSCRIPTION_ID" | gh secret set AZURE_SUBSCRIPTION_ID --repo "$GITHUB_REPOSITORY_NAME"

gh variable set AZURE_RESOURCE_GROUP --body "$HOMS_AZURE_RESOURCE_GROUP" --repo "$GITHUB_REPOSITORY_NAME"
gh variable set AZURE_LOCATION --body "$HOMS_AZURE_LOCATION" --repo "$GITHUB_REPOSITORY_NAME"
gh variable set AZURE_CONTAINER_REGISTRY --body "$HOMS_AZURE_ACR_NAME" --repo "$GITHUB_REPOSITORY_NAME"
gh variable set AZURE_CONTAINERAPPS_ENVIRONMENT --body "$HOMS_CONTAINERAPPS_ENVIRONMENT" --repo "$GITHUB_REPOSITORY_NAME"
gh variable set AZURE_STORAGE_ACCOUNT --body "$HOMS_AZURE_STORAGE_ACCOUNT" --repo "$GITHUB_REPOSITORY_NAME"
gh variable set AZURE_APP_PREFIX --body "$HOMS_APP_PREFIX" --repo "$GITHUB_REPOSITORY_NAME"
gh variable set AZURE_ACR_PULL_IDENTITY --body "$HOMS_ACR_PULL_IDENTITY" --repo "$GITHUB_REPOSITORY_NAME"

log "OIDC setup complete. No client password was created."

