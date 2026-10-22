#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=azure-common.sh
source "$SCRIPT_DIR/azure-common.sh"

DRY_RUN=false
FOUNDATION_STUDENTS=(1 2 3 4 5)

usage() {
  cat <<'EOF'
Create or reuse the shared HOMS R2 Azure foundation.

Usage:
  AZURE_SUBSCRIPTION_ID=<id> \
  HOMS_AZURE_ACR_NAME=<globally-unique-name> \
  HOMS_AZURE_STORAGE_ACCOUNT=<globally-unique-name> \
  ./scripts/deploy/azure-foundation.sh [--student STUDENT] [--dry-run]

Optional environment variables:
  HOMS_AZURE_LOCATION                default: australiaeast
  HOMS_AZURE_RESOURCE_GROUP          default: homs-r2-rg
  HOMS_CONTAINERAPPS_ENVIRONMENT     default: homs-r2-env
  HOMS_LOG_ANALYTICS_WORKSPACE       default: homs-r2-logs
  HOMS_ACR_PULL_IDENTITY             default: homs-r2-acr-pull
  HOMS_APP_PREFIX                    default: homs-r2

The script is additive and idempotent where Azure permits. It never deletes a
resource. --dry-run performs read-only inspection when the Azure CLI is already
authenticated to AZURE_SUBSCRIPTION_ID, then labels each resource as existing
or missing. It never creates or changes a resource in dry-run mode.

Required Azure resource providers must already be registered by the
subscription owner. The script does not attempt subscription-level provider
registration. Use --student to limit database share/storage-link work to one
student while leaving shared foundation resources reusable by the whole team.
EOF
}

while (($#)); do
  case "$1" in
    --student)
      (($# >= 2)) || die "--student requires a value."
      validate_student "$2"
      FOUNDATION_STUDENTS=("$2")
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

if [[ "$DRY_RUN" == true ]]; then
  log "Dry run: no Azure or GitHub resource will be changed."
  log "Subscription: configured"
  log "Location: $HOMS_AZURE_LOCATION"

  CURRENT_SUBSCRIPTION=""
  if command -v az >/dev/null 2>&1; then
    CURRENT_SUBSCRIPTION="$(az account show --query id --output tsv --only-show-errors 2>/dev/null || true)"
  fi

  if [[ "$CURRENT_SUBSCRIPTION" != "$AZURE_SUBSCRIPTION_ID" ]]; then
    log "Read-only Azure inspection skipped: authenticate/select the configured subscription to see exists/missing status."
    log "[planned] Resource group: $HOMS_AZURE_RESOURCE_GROUP"
    log "[planned] Container registry: $HOMS_AZURE_ACR_NAME"
    log "[planned] Container Apps environment: $HOMS_CONTAINERAPPS_ENVIRONMENT"
    log "[planned] Log Analytics workspace: $HOMS_LOG_ANALYTICS_WORKSPACE"
    log "[planned] Storage account: $HOMS_AZURE_STORAGE_ACCOUNT"
    log "[planned] ACR pull identity: $HOMS_ACR_PULL_IDENTITY"
    for student in "${FOUNDATION_STUDENTS[@]}"; do
      log "[planned] Student $student storage: $(database_share_name "$student") -> $(environment_storage_name "$student")"
    done
    exit 0
  fi

  report_resource() {
    local label="$1"
    shift
    if "$@" >/dev/null 2>&1; then
      log "[exists] $label (would reuse)"
    else
      log "[missing] $label (would create)"
    fi
  }

  report_resource "Resource group $HOMS_AZURE_RESOURCE_GROUP" \
    az group show --name "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors
  report_resource "Container registry $HOMS_AZURE_ACR_NAME" \
    az acr show --name "$HOMS_AZURE_ACR_NAME" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors
  if az containerapp env show --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --only-show-errors >/dev/null 2>&1; then
    log "[exists] Container Apps environment $HOMS_CONTAINERAPPS_ENVIRONMENT (would reuse)"
    log "[unchanged] Existing environment logging configuration (workspace creation not needed)"
  else
    log "[missing] Container Apps environment $HOMS_CONTAINERAPPS_ENVIRONMENT (would create)"
    report_resource "Log Analytics workspace $HOMS_LOG_ANALYTICS_WORKSPACE" \
      az monitor log-analytics workspace show \
        --workspace-name "$HOMS_LOG_ANALYTICS_WORKSPACE" \
        --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors
  fi
  report_resource "Storage account $HOMS_AZURE_STORAGE_ACCOUNT" \
    az storage account show --name "$HOMS_AZURE_STORAGE_ACCOUNT" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors
  report_resource "ACR pull identity $HOMS_ACR_PULL_IDENTITY" \
    az identity show --name "$HOMS_ACR_PULL_IDENTITY" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors

  for student in "${FOUNDATION_STUDENTS[@]}"; do
    share_name="$(database_share_name "$student")"
    storage_name="$(environment_storage_name "$student")"
    report_resource "Azure Files share $share_name" \
      az storage share-rm show --storage-account "$HOMS_AZURE_STORAGE_ACCOUNT" \
        --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
        --name "$share_name" --only-show-errors
    if [[ "$(az containerapp env storage list \
      --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
      --query "[?name=='$storage_name'] | length(@)" \
      --output tsv --only-show-errors 2>/dev/null || true)" == "1" ]]; then
      log "[exists] Container Apps storage link $storage_name (would update/reuse)"
    else
      log "[missing] Container Apps storage link $storage_name (would create)"
    fi
  done
  exit 0
fi

azure_account_check
select_subscription

log "Using resource providers pre-registered by the subscription owner."

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

if ! az containerapp env show \
  --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
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
  unset LOG_WORKSPACE_KEY
else
  log "Reusing Container Apps environment $HOMS_CONTAINERAPPS_ENVIRONMENT without changing its logging configuration."
fi

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

for student in "${FOUNDATION_STUDENTS[@]}"; do
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

