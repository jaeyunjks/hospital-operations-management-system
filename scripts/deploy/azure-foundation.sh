#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=azure-common.sh
source "$SCRIPT_DIR/azure-common.sh"

DRY_RUN=false

usage() {
  cat <<'EOF'
Create or reuse the shared HOMS R2 Azure foundation.

Usage:
  AZURE_SUBSCRIPTION_ID=<id> \
  HOMS_AZURE_ACR_NAME=<globally-unique-name> \
  HOMS_AZURE_STORAGE_ACCOUNT=<globally-unique-name> \
  ./scripts/deploy/azure-foundation.sh [--dry-run]

Optional environment variables:
  HOMS_AZURE_LOCATION                default: australiaeast
  HOMS_AZURE_RESOURCE_GROUP          default: homs-r2-rg
  HOMS_CONTAINERAPPS_ENVIRONMENT     default: homs-r2-env
  HOMS_LOG_ANALYTICS_WORKSPACE       default: homs-r2-logs
  HOMS_ACR_PULL_IDENTITY             default: homs-r2-acr-pull
  HOMS_APP_PREFIX                    default: homs-r2

The script is additive and idempotent where Azure permits. It never deletes a
resource. --dry-run validates inputs and prints the intended resource names
without calling Azure.
EOF
}

while (($#)); do
  case "$1" in
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

if [[ "$DRY_RUN" == true ]]; then
  log "Dry run: no Azure command will be executed."
  log "Subscription: configured"
  log "Location: $HOMS_AZURE_LOCATION"
  log "Resource group: $HOMS_AZURE_RESOURCE_GROUP"
  log "Container registry: $HOMS_AZURE_ACR_NAME"
  log "Container Apps environment: $HOMS_CONTAINERAPPS_ENVIRONMENT"
  log "Log Analytics workspace: $HOMS_LOG_ANALYTICS_WORKSPACE"
  log "Storage account: $HOMS_AZURE_STORAGE_ACCOUNT"
  log "ACR pull identity: $HOMS_ACR_PULL_IDENTITY"
  for student in 1 2 3 4 5; do
    log "Student $student storage: $(database_share_name "$student") -> $(environment_storage_name "$student")"
  done
  exit 0
fi

azure_account_check
select_subscription

log "Registering required Azure resource providers."
az provider register --namespace Microsoft.App --wait --only-show-errors
az provider register --namespace Microsoft.OperationalInsights --wait --only-show-errors
az provider register --namespace Microsoft.ContainerRegistry --wait --only-show-errors
az provider register --namespace Microsoft.Storage --wait --only-show-errors
az provider register --namespace Microsoft.ManagedIdentity --wait --only-show-errors

log "Creating or reusing resource group $HOMS_AZURE_RESOURCE_GROUP."
az group create \
  --name "$HOMS_AZURE_RESOURCE_GROUP" \
  --location "$HOMS_AZURE_LOCATION" \
  --tags application=homs release=r2 managed-by=homs-deployment-scripts \
  --only-show-errors --output none

if ! az acr show --name "$HOMS_AZURE_ACR_NAME" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  log "Creating Azure Container Registry $HOMS_AZURE_ACR_NAME."
  az acr create \
    --name "$HOMS_AZURE_ACR_NAME" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --location "$HOMS_AZURE_LOCATION" \
    --sku Basic \
    --admin-enabled false \
    --tags application=homs release=r2 \
    --only-show-errors --output none
else
  log "Reusing Azure Container Registry $HOMS_AZURE_ACR_NAME."
fi

if ! az monitor log-analytics workspace show \
  --workspace-name "$HOMS_LOG_ANALYTICS_WORKSPACE" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  log "Creating Log Analytics workspace $HOMS_LOG_ANALYTICS_WORKSPACE."
  az monitor log-analytics workspace create \
    --workspace-name "$HOMS_LOG_ANALYTICS_WORKSPACE" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --location "$HOMS_AZURE_LOCATION" \
    --tags application=homs release=r2 \
    --only-show-errors --output none
else
  log "Reusing Log Analytics workspace $HOMS_LOG_ANALYTICS_WORKSPACE."
fi

LOG_WORKSPACE_ID="$(az monitor log-analytics workspace show \
  --workspace-name "$HOMS_LOG_ANALYTICS_WORKSPACE" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query customerId --output tsv --only-show-errors)"
LOG_WORKSPACE_KEY="$(az monitor log-analytics workspace get-shared-keys \
  --workspace-name "$HOMS_LOG_ANALYTICS_WORKSPACE" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query primarySharedKey --output tsv --only-show-errors)"

if ! az containerapp env show \
  --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  log "Creating Container Apps environment $HOMS_CONTAINERAPPS_ENVIRONMENT."
  az containerapp env create \
    --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --location "$HOMS_AZURE_LOCATION" \
    --logs-destination log-analytics \
    --logs-workspace-id "$LOG_WORKSPACE_ID" \
    --logs-workspace-key "$LOG_WORKSPACE_KEY" \
    --tags application=homs release=r2 \
    --only-show-errors --output none
else
  log "Reusing Container Apps environment $HOMS_CONTAINERAPPS_ENVIRONMENT."
fi
unset LOG_WORKSPACE_KEY

if ! az storage account show \
  --name "$HOMS_AZURE_STORAGE_ACCOUNT" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  log "Creating storage account $HOMS_AZURE_STORAGE_ACCOUNT."
  az storage account create \
    --name "$HOMS_AZURE_STORAGE_ACCOUNT" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --location "$HOMS_AZURE_LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --https-only true \
    --min-tls-version TLS1_2 \
    --allow-blob-public-access false \
    --tags application=homs release=r2 \
    --only-show-errors --output none
else
  log "Reusing storage account $HOMS_AZURE_STORAGE_ACCOUNT."
fi

STORAGE_ACCOUNT_KEY="$(az storage account keys list \
  --account-name "$HOMS_AZURE_STORAGE_ACCOUNT" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query '[0].value' --output tsv --only-show-errors)"

for student in 1 2 3 4 5; do
  share_name="$(database_share_name "$student")"
  storage_name="$(environment_storage_name "$student")"
  if ! az storage share-rm show \
    --storage-account "$HOMS_AZURE_STORAGE_ACCOUNT" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --name "$share_name" --only-show-errors >/dev/null 2>&1; then
    log "Creating dedicated Azure Files share $share_name."
    az storage share-rm create \
      --storage-account "$HOMS_AZURE_STORAGE_ACCOUNT" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
      --name "$share_name" \
      --quota 5 \
      --access-tier TransactionOptimized \
      --only-show-errors --output none
  else
    log "Reusing dedicated Azure Files share $share_name."
  fi

  log "Creating or updating Container Apps storage link $storage_name."
  az containerapp env storage set \
    --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --storage-name "$storage_name" \
    --access-mode ReadWrite \
    --azure-file-account-name "$HOMS_AZURE_STORAGE_ACCOUNT" \
    --azure-file-account-key "$STORAGE_ACCOUNT_KEY" \
    --azure-file-share-name "$share_name" \
    --only-show-errors --output none
done
unset STORAGE_ACCOUNT_KEY

if ! az identity show \
  --name "$HOMS_ACR_PULL_IDENTITY" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
  log "Creating shared ACR pull identity $HOMS_ACR_PULL_IDENTITY."
  az identity create \
    --name "$HOMS_ACR_PULL_IDENTITY" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --location "$HOMS_AZURE_LOCATION" \
    --tags application=homs release=r2 purpose=acr-pull \
    --only-show-errors --output none
else
  log "Reusing shared ACR pull identity $HOMS_ACR_PULL_IDENTITY."
fi

ACR_ID="$(az acr show --name "$HOMS_AZURE_ACR_NAME" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query id --output tsv --only-show-errors)"
PULL_PRINCIPAL_ID="$(az identity show --name "$HOMS_ACR_PULL_IDENTITY" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query principalId --output tsv --only-show-errors)"
if [[ "$(az role assignment list --assignee-object-id "$PULL_PRINCIPAL_ID" \
  --scope "$ACR_ID" --role AcrPull --query 'length(@)' --output tsv --only-show-errors)" == "0" ]]; then
  log "Granting AcrPull to the shared image-pull identity."
  az role assignment create \
    --assignee-object-id "$PULL_PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role AcrPull \
    --scope "$ACR_ID" \
    --only-show-errors --output none
else
  log "AcrPull assignment already exists."
fi

ENVIRONMENT_ID="$(az containerapp env show \
  --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query id --output tsv --only-show-errors)"
IDENTITY_ID="$(az identity show \
  --name "$HOMS_ACR_PULL_IDENTITY" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query id --output tsv --only-show-errors)"
ACR_LOGIN_SERVER="$(az acr show \
  --name "$HOMS_AZURE_ACR_NAME" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query loginServer --output tsv --only-show-errors)"

log "Shared foundation is ready. Non-secret deployment identifiers:"
printf 'resource_group=%s\n' "$HOMS_AZURE_RESOURCE_GROUP"
printf 'location=%s\n' "$HOMS_AZURE_LOCATION"
printf 'container_apps_environment=%s\n' "$HOMS_CONTAINERAPPS_ENVIRONMENT"
printf 'container_apps_environment_id=%s\n' "$ENVIRONMENT_ID"
printf 'container_registry=%s\n' "$HOMS_AZURE_ACR_NAME"
printf 'container_registry_server=%s\n' "$ACR_LOGIN_SERVER"
printf 'storage_account=%s\n' "$HOMS_AZURE_STORAGE_ACCOUNT"
printf 'acr_pull_identity_id=%s\n' "$IDENTITY_ID"

