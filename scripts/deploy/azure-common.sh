#!/usr/bin/env bash

# Shared, non-secret configuration and validation helpers for HOMS R2 Azure
# deployment scripts. This file is sourced; it is not intended to be run.

set -euo pipefail

HOMS_AZURE_LOCATION="${HOMS_AZURE_LOCATION:-australiaeast}"
HOMS_AZURE_RESOURCE_GROUP="${HOMS_AZURE_RESOURCE_GROUP:-homs-r2-rg}"
HOMS_CONTAINERAPPS_ENVIRONMENT="${HOMS_CONTAINERAPPS_ENVIRONMENT:-homs-r2-env}"
HOMS_LOG_ANALYTICS_WORKSPACE="${HOMS_LOG_ANALYTICS_WORKSPACE:-homs-r2-logs}"
HOMS_ACR_PULL_IDENTITY="${HOMS_ACR_PULL_IDENTITY:-homs-r2-acr-pull}"
HOMS_APP_PREFIX="${HOMS_APP_PREFIX:-homs-r2}"
HOMS_GITHUB_ENVIRONMENT="${HOMS_GITHUB_ENVIRONMENT:-azure-r2}"

log() {
  printf '[HOMS-AZURE] %s\n' "$*"
}

die() {
  printf '[HOMS-AZURE] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command '$1' is not installed."
}

require_value() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" ]] || die "$name must be set."
}

validate_student() {
  [[ "${1:-}" =~ ^[1-5]$ ]] || die "Student must be one of: 1, 2, 3, 4, 5."
}

validate_acr_name() {
  [[ "$1" =~ ^[a-z0-9]{5,50}$ ]] || die \
    "HOMS_AZURE_ACR_NAME must be 5-50 lowercase letters/numbers (globally unique)."
}

validate_storage_name() {
  [[ "$1" =~ ^[a-z0-9]{3,24}$ ]] || die \
    "HOMS_AZURE_STORAGE_ACCOUNT must be 3-24 lowercase letters/numbers (globally unique)."
}

container_app_name() {
  printf '%s-student%s-%s' "$HOMS_APP_PREFIX" "$1" "$2"
}

database_share_name() {
  printf 'student%s-db' "$1"
}

environment_storage_name() {
  printf 'student%s-db-storage' "$1"
}

azure_account_check() {
  require_command az
  az account show --query id --output tsv --only-show-errors >/dev/null 2>&1 || die \
    "Azure CLI is not authenticated. Run 'az login' first."
}

select_subscription() {
  require_value AZURE_SUBSCRIPTION_ID
  az account set --subscription "$AZURE_SUBSCRIPTION_ID" --only-show-errors
  local selected
  selected="$(az account show --query id --output tsv --only-show-errors)"
  [[ "$selected" == "$AZURE_SUBSCRIPTION_ID" ]] || die \
    "Azure CLI did not select the requested subscription."
}

