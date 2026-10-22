#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=azure-common.sh
source "$SCRIPT_DIR/azure-common.sh"

DRY_RUN=false
GITHUB_REPOSITORY_NAME="${GITHUB_REPOSITORY:-}"
HOMS_OIDC_IDENTITY_NAME="${HOMS_OIDC_IDENTITY_NAME:-homs-r2-github}"

usage() {
  cat <<'EOF'
Verify the pre-created managed-identity GitHub OIDC configuration read-only.

Usage:
  AZURE_SUBSCRIPTION_ID=<id> \
  GITHUB_REPOSITORY=owner/repository \
  ./scripts/deploy/github-oidc-setup.sh [--dry-run]

Supported Release 2 authentication model:
  - pre-created user-assigned managed identity (default: homs-r2-github)
  - GitHub federated credentials for the develop and main branch subjects
  - Contributor at the homs-r2-rg resource-group scope
  - repository secrets AZURE_CLIENT_ID, AZURE_TENANT_ID and
    AZURE_SUBSCRIPTION_ID

This helper is verification-only. It never creates an Entra application,
service principal, managed identity, federated credential, role assignment,
GitHub secret, variable or environment.

Options:
  --repository owner/repository
  --dry-run       print the expected contract without querying Azure/GitHub
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
[[ "$GITHUB_REPOSITORY_NAME" =~ ^[^/]+/[^/]+$ ]] || die \
  "GITHUB_REPOSITORY must use owner/repository format."

DEVELOP_SUBJECT="repo:${GITHUB_REPOSITORY_NAME}:ref:refs/heads/develop"
MAIN_SUBJECT="repo:${GITHUB_REPOSITORY_NAME}:ref:refs/heads/main"

if [[ "$DRY_RUN" == true ]]; then
  log "Dry run: read-only OIDC verification contract; no setting will be changed."
  log "Managed identity: $HOMS_OIDC_IDENTITY_NAME"
  log "Required federated subjects: $DEVELOP_SUBJECT, $MAIN_SUBJECT"
  log "Required role: Contributor on $HOMS_AZURE_RESOURCE_GROUP"
  log "Required GitHub secret names: AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID"
  exit 0
fi

azure_account_check
SELECTED_SUBSCRIPTION_ID="$(az account show \
  --query id --output tsv --only-show-errors)"
[[ "$SELECTED_SUBSCRIPTION_ID" == "$AZURE_SUBSCRIPTION_ID" ]] || die \
  "Azure CLI is not on AZURE_SUBSCRIPTION_ID; select it before verification."
require_command gh
require_command python3
gh auth status >/dev/null 2>&1 || die \
  "GitHub CLI is not authenticated. Run 'gh auth login' first."

IDENTITY_JSON="$(az identity show \
  --name "$HOMS_OIDC_IDENTITY_NAME" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query '{clientId:clientId,principalId:principalId}' \
  --output json --only-show-errors)" || die \
  "Managed identity '$HOMS_OIDC_IDENTITY_NAME' was not found."

CLIENT_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["clientId"])' "$IDENTITY_JSON")"
PRINCIPAL_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["principalId"])' "$IDENTITY_JSON")"
[[ -n "$CLIENT_ID" && -n "$PRINCIPAL_ID" ]] || die \
  "Managed identity '$HOMS_OIDC_IDENTITY_NAME' has incomplete identifiers."

FEDERATED_JSON="$(az identity federated-credential list \
  --identity-name "$HOMS_OIDC_IDENTITY_NAME" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --output json --only-show-errors)"
python3 - "$FEDERATED_JSON" "$DEVELOP_SUBJECT" "$MAIN_SUBJECT" <<'PY'
import json
import sys

credentials = json.loads(sys.argv[1])
required_subjects = set(sys.argv[2:])
valid = {
    item.get("subject")
    for item in credentials
    if item.get("issuer", "").rstrip("/")
    == "https://token.actions.githubusercontent.com"
    and "api://AzureADTokenExchange" in item.get("audiences", [])
}
missing = required_subjects - valid
if missing:
    raise SystemExit(
        "Missing valid managed-identity federated subject(s): "
        + ", ".join(sorted(missing))
    )
PY

RESOURCE_GROUP_ID="$(az group show \
  --name "$HOMS_AZURE_RESOURCE_GROUP" \
  --query id --output tsv --only-show-errors)"
ROLE_COUNT="$(az role assignment list \
  --assignee-object-id "$PRINCIPAL_ID" \
  --scope "$RESOURCE_GROUP_ID" \
  --role Contributor \
  --query 'length(@)' --output tsv --only-show-errors)"
[[ "$ROLE_COUNT" -gt 0 ]] || die \
  "Managed identity '$HOMS_OIDC_IDENTITY_NAME' lacks Contributor on $HOMS_AZURE_RESOURCE_GROUP."

SECRET_NAMES="$(gh api \
  "repos/${GITHUB_REPOSITORY_NAME}/actions/secrets" \
  --jq '.secrets[].name')"
for secret_name in AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID; do
  grep -Fxq "$secret_name" <<<"$SECRET_NAMES" || die \
    "Required repository secret '$secret_name' is not visible to the current GitHub account."
done

log "Verified managed-identity OIDC configuration for develop and main."
log "Verified Contributor at the shared resource-group scope."
log "Verified all three GitHub OIDC secret names without reading their values."
log "No Azure or GitHub setting was changed."
