#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=azure-common.sh
source "$SCRIPT_DIR/azure-common.sh"

DRY_RUN=false
IMAGE_TAG=""
STUDENT=""

usage() {
  cat <<'EOF'
Build, push and deploy exactly one student's frontend/backend/database slice.

Usage:
  ./scripts/deploy/deploy-student.sh STUDENT [--tag IMAGE_TAG] [--dry-run]

Required environment variables:
  AZURE_SUBSCRIPTION_ID
  HOMS_AZURE_ACR_NAME
  HOMS_AZURE_STORAGE_ACCOUNT

The shared foundation and OIDC setup must already exist. Images use immutable
commit-SHA tags and are built with local Docker, then pushed after az acr login.
This script never uses ACR Tasks, deletes an app, or touches another student's
uniquely named resources.
EOF
}

while (($#)); do
  case "$1" in
    --tag)
      (($# >= 2)) || die "--tag requires a value."
      IMAGE_TAG="$2"
      shift
      ;;
    --dry-run) DRY_RUN=true ;;
    --help|-h) usage; exit 0 ;;
    -*) die "Unknown argument: $1" ;;
    *)
      [[ -z "$STUDENT" ]] || die "Only one student number may be supplied."
      STUDENT="$1"
      ;;
  esac
  shift
done

validate_student "$STUDENT"
require_value AZURE_SUBSCRIPTION_ID
require_value HOMS_AZURE_ACR_NAME
require_value HOMS_AZURE_STORAGE_ACCOUNT
validate_acr_name "$HOMS_AZURE_ACR_NAME"
validate_storage_name "$HOMS_AZURE_STORAGE_ACCOUNT"
require_command python3
require_command git

if [[ -z "$IMAGE_TAG" ]]; then
  IMAGE_TAG="$(git -C "$REPOSITORY_ROOT" rev-parse HEAD)"
fi
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{7,40}$ ]] || die \
  "Image tag must be a 7-40 character lowercase Git commit SHA."

build_context() {
  case "$1:$2" in
    1:*) printf 'student-1' ;;
    2:frontend|3:*|4:frontend) printf '.' ;;
    2:backend) printf 'student-2/backend' ;;
    2:database) printf 'student-2/database' ;;
    4:backend) printf 'student-4/backend' ;;
    4:database) printf 'student-4/database' ;;
    5:*) printf 'student-5/%s' "$2" ;;
    *) die "No build context for Student $1 $2." ;;
  esac
}

dockerfile_path() {
  case "$1:$2" in
    1:*) printf 'student-1/%s/Dockerfile' "$2" ;;
    2:frontend) printf 'student-2/frontend/Dockerfile' ;;
    2:backend|2:database|4:backend|4:database|5:*) printf 'student-%s/%s/Dockerfile' "$1" "$2" ;;
    3:*) printf 'student-%s/%s/Dockerfile' "$1" "$2" ;;
    4:frontend) printf 'student-4/frontend/Dockerfile' ;;
    *) die "No Dockerfile for Student $1 $2." ;;
  esac
}

for service in frontend backend database; do
  context="$(build_context "$STUDENT" "$service")"
  dockerfile="$(dockerfile_path "$STUDENT" "$service")"
  [[ -d "$REPOSITORY_ROOT/$context" ]] || die "Missing build context: $context"
  [[ -f "$REPOSITORY_ROOT/$dockerfile" ]] || die \
    "Missing Dockerfile for Student $STUDENT $service: $dockerfile"
done

DATABASE_PORT=$((6000 + STUDENT * 100))
BACKEND_PORT=$((5000 + STUDENT * 100))
FRONTEND_PORT=$((3000 + STUDENT * 100))
DATABASE_APP="$(container_app_name "$STUDENT" database)"
BACKEND_APP="$(container_app_name "$STUDENT" backend)"
FRONTEND_APP="$(container_app_name "$STUDENT" frontend)"
STORAGE_NAME="$(environment_storage_name "$STUDENT")"

if [[ "$DRY_RUN" == true ]]; then
  log "Dry run: no image will be built or pushed and no Container App will change."
  log "Student: $STUDENT"
  log "Immutable image tag: $IMAGE_TAG"
  for service in database backend frontend; do
    image="$HOMS_AZURE_ACR_NAME.azurecr.io/student$STUDENT/$service:$IMAGE_TAG"
    extra_context=""
    if [[ "$STUDENT:$service" == "5:frontend" ]]; then
      extra_context=" --build-context shared=shared/frontend"
    fi
    log "$service image: $image"
    log "$service build: context=$(build_context "$STUDENT" "$service") dockerfile=$(dockerfile_path "$STUDENT" "$service")"
    log "$service command: docker build -t $image -f $(dockerfile_path "$STUDENT" "$service")$extra_context $(build_context "$STUDENT" "$service")"
    log "$service push: docker push $image"
    log "$service app: $(container_app_name "$STUDENT" "$service")"
  done
  log "Database storage: $STORAGE_NAME mounted at /data; replicas fixed at 1."
  log "Ingress: frontend=external, backend=internal, database=internal."
  log "Cloud baseline: AI_ENABLED=false, MCP_ENABLED=false, RAG_ENABLED=false, MULTI_AGENT_ENABLED=false."
  if [[ "$STUDENT" == "5" ]]; then
    log "Lifecycle: quiesce s5-db in multiple-revision mode, then require database and API health before continuing."
  fi
  exit 0
fi

azure_account_check
select_subscription

az group show --name "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1 || die \
  "Shared resource group is missing. Run azure-foundation.sh first."
az acr show --name "$HOMS_AZURE_ACR_NAME" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1 || die \
  "Shared container registry is missing. Run azure-foundation.sh first."
az containerapp env show --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1 || die \
  "Shared Container Apps environment is missing. Run azure-foundation.sh first."
az identity show --name "$HOMS_ACR_PULL_IDENTITY" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1 || die \
  "Shared ACR pull identity is missing. Run azure-foundation.sh first."

if [[ "$(az containerapp env storage list \
  --name "$HOMS_CONTAINERAPPS_ENVIRONMENT" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query "[?name=='$STORAGE_NAME'] | length(@)" \
  --output tsv --only-show-errors)" == "0" ]]; then
  die "Storage link '$STORAGE_NAME' is missing. Run azure-foundation.sh first."
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

require_command docker
log "Authenticating Docker to $ACR_LOGIN_SERVER using the existing Azure OIDC session."
az acr login --name "$HOMS_AZURE_ACR_NAME" --only-show-errors

DATABASE_IMAGE="$ACR_LOGIN_SERVER/student$STUDENT/database:$IMAGE_TAG"
BACKEND_IMAGE="$ACR_LOGIN_SERVER/student$STUDENT/backend:$IMAGE_TAG"
FRONTEND_IMAGE="$ACR_LOGIN_SERVER/student$STUDENT/frontend:$IMAGE_TAG"

image_uri() {
  case "$1" in
    database) printf '%s' "$DATABASE_IMAGE" ;;
    backend) printf '%s' "$BACKEND_IMAGE" ;;
    frontend) printf '%s' "$FRONTEND_IMAGE" ;;
    *) die "Unknown service image: $1" ;;
  esac
}

for service in database backend frontend; do
  context="$(build_context "$STUDENT" "$service")"
  dockerfile="$(dockerfile_path "$STUDENT" "$service")"
  build_command=(
    docker build
    -t "$(image_uri "$service")"
    -f "$dockerfile"
  )
  if [[ "$STUDENT:$service" == "5:frontend" ]]; then
    build_command+=(--build-context "shared=$REPOSITORY_ROOT/shared/frontend")
  fi
  build_command+=("$context")

  log "Building $(image_uri "$service") locally with Docker."
  (
    cd "$REPOSITORY_ROOT"
    "${build_command[@]}"
  )
  log "Pushing $(image_uri "$service") to $ACR_LOGIN_SERVER."
  docker push "$(image_uri "$service")"
done

upsert_app() {
  local app_name="$1"
  local service="$2"
  local port="$3"
  local ingress="$4"
  local health_path="$5"
  local storage_name="$6"
  shift 6
  local env_settings=("$@")
  local config_file
  config_file="$(mktemp)"

  local render=(
    python3 "$SCRIPT_DIR/render-container-app.py"
    --name "$app_name"
    --location "$HOMS_AZURE_LOCATION"
    --environment-id "$ENVIRONMENT_ID"
    --identity-id "$IDENTITY_ID"
    --registry-server "$ACR_LOGIN_SERVER"
    --image "$(image_uri "$service")"
    --container-name "$service"
    --port "$port"
    --ingress "$ingress"
    --health-path "$health_path"
    --min-replicas 1
    --max-replicas 1
  )
  if [[ -n "$storage_name" ]]; then
    render+=(--storage-name "$storage_name" --mount-path /data)
  fi
  local env_setting
  for env_setting in "${env_settings[@]}"; do
    render+=(--env "$env_setting")
  done
  "${render[@]}" >"$config_file"

  if az containerapp show --name "$app_name" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" --only-show-errors >/dev/null 2>&1; then
    log "Updating only $app_name."
    if [[ "$STUDENT" == "5" ]]; then
      # Full-YAML CLI updates can lose a managed-identity reference when the
      # database has deliberately been quiesced and has no ready revision.
      # Patch only the desired template through ARM: identity, registry,
      # ingress and environment storage configuration remain untouched, while
      # image, env, mount, scale and probe changes are all applied together in
      # one new revision.
      local patch_file resource_id
      patch_file="$(mktemp)"
      python3 "$SCRIPT_DIR/render-container-app-template-patch.py" \
        "$config_file" >"$patch_file"
      resource_id="$(az containerapp show \
        --name "$app_name" \
        --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
        --query id --output tsv --only-show-errors)"
      az rest \
        --method patch \
        --url "https://management.azure.com${resource_id}?api-version=2025-01-01" \
        --body "@$patch_file" \
        --only-show-errors --output none
      rm -f "$patch_file"
    else
      az containerapp update \
        --name "$app_name" \
        --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
        --yaml "$config_file" \
        --only-show-errors --output none
    fi
  else
    log "Creating $app_name."
    az containerapp create \
      --name "$app_name" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
      --yaml "$config_file" \
      --only-show-errors --output none
  fi
  rm -f "$config_file"
}

wait_for_revision_replicas_to_stop() {
  local app_name="$1"
  local revision="$2"
  local live_replicas
  for _attempt in {1..24}; do
    live_replicas="$(az containerapp replica list \
      --name "$app_name" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
      --revision "$revision" \
      --query "[?properties.runningState!='NotRunning'] | length(@)" \
      --output tsv --only-show-errors 2>/dev/null || printf '0')"
    if [[ "$live_replicas" == "0" ]]; then
      return
    fi
    sleep 5
  done
  die "Timed out waiting for $app_name revision $revision to stop."
}

quiesce_student5_database() {
  if ! az containerapp show --name "$DATABASE_APP" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --only-show-errors >/dev/null 2>&1; then
    return
  fi

  # Multiple mode prevents Container Apps from reactivating the last healthy
  # database revision as a rollback target while the replacement starts.
  log "Switching $DATABASE_APP to multiple-revision mode for a zero-overlap update."
  az containerapp revision set-mode \
    --name "$DATABASE_APP" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --mode multiple \
    --only-show-errors --output none

  local active_revisions revision
  active_revisions="$(az containerapp revision list --all \
    --name "$DATABASE_APP" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --query "[?properties.active].name" --output tsv --only-show-errors)"
  while IFS= read -r revision; do
    [[ -n "$revision" ]] || continue
    log "Deactivating $DATABASE_APP revision $revision before replacement."
    az containerapp revision deactivate \
      --name "$DATABASE_APP" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
      --revision "$revision" --only-show-errors >/dev/null
  done <<<"$active_revisions"

  local revisions
  revisions="$(az containerapp revision list --all \
    --name "$DATABASE_APP" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --query "[].name" --output tsv --only-show-errors)"
  while IFS= read -r revision; do
    [[ -n "$revision" ]] || continue
    wait_for_revision_replicas_to_stop "$DATABASE_APP" "$revision"
  done <<<"$revisions"

  # Give Azure Files time to release SMB handles after the last replica exits.
  sleep 30
}

retire_other_student5_database_revisions() {
  local latest_revision active_revisions revision
  latest_revision="$(az containerapp show \
    --name "$DATABASE_APP" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --query properties.latestRevisionName --output tsv --only-show-errors)"
  [[ -n "$latest_revision" ]] || die \
    "Azure did not return the new revision for $DATABASE_APP."

  active_revisions="$(az containerapp revision list --all \
    --name "$DATABASE_APP" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --query "[?properties.active].name" --output tsv --only-show-errors)"
  while IFS= read -r revision; do
    [[ -n "$revision" && "$revision" != "$latest_revision" ]] || continue
    log "Deactivating unexpected overlapping $DATABASE_APP revision $revision."
    az containerapp revision deactivate \
      --name "$DATABASE_APP" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
      --revision "$revision" --only-show-errors >/dev/null
    wait_for_revision_replicas_to_stop "$DATABASE_APP" "$revision"
  done <<<"$active_revisions"
}

wait_for_app_healthy() {
  local app_name="$1"
  local active_revision health ready_replicas
  log "Waiting for $app_name to report one healthy, ready replica."
  for _attempt in {1..48}; do
    active_revision="$(az containerapp revision list \
      --name "$app_name" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
      --query "[?properties.active].name | [0]" \
      --output tsv --only-show-errors)"
    if [[ -n "$active_revision" ]]; then
      health="$(az containerapp revision show \
        --name "$app_name" \
        --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
        --revision "$active_revision" \
        --query properties.healthState --output tsv --only-show-errors)"
      ready_replicas="$(az containerapp replica list \
        --name "$app_name" \
        --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
        --revision "$active_revision" \
        --query "[?properties.containers[0].ready==\`true\`] | length(@)" \
        --output tsv --only-show-errors)"
      if [[ "$health" == "Healthy" && "$ready_replicas" == "1" ]]; then
        log "$app_name is healthy on revision $active_revision."
        return
      fi
    fi
    sleep 5
  done
  die "$app_name did not become healthy with exactly one ready replica."
}

case "$STUDENT" in
  1) DATABASE_ENV=("PORT=$DATABASE_PORT" "HOST=0.0.0.0" "FLASK_DEBUG=0" "DB_PATH=/data/patients.db") ;;
  2) DATABASE_ENV=("PORT=$DATABASE_PORT" "DATABASE_PATH=/data/clinical_staff.db") ;;
  3) DATABASE_ENV=("PORT=$DATABASE_PORT" "STUDENT3_DATABASE_PATH=/data/pharmacy.db") ;;
  4) DATABASE_ENV=("PORT=$DATABASE_PORT" "FLASK_DEBUG=0" "DB_PATH=/data/rooms.db") ;;
  5) DATABASE_ENV=(
       "DATABASE_SERVICE_PORT=$DATABASE_PORT"
       "STUDENT5_DATABASE_PATH=/data/staff_shift.db"
       "STUDENT5_SQLITE_NOLOCK=true"
     ) ;;
esac

if [[ "$STUDENT" == "5" ]]; then
  quiesce_student5_database
fi
DATABASE_HEALTH_PATH=/health
[[ "$STUDENT" == "5" ]] && DATABASE_HEALTH_PATH=/live
upsert_app "$DATABASE_APP" database "$DATABASE_PORT" internal "$DATABASE_HEALTH_PATH" "$STORAGE_NAME" \
  "${DATABASE_ENV[@]}"
if [[ "$STUDENT" == "5" ]]; then
  retire_other_student5_database_revisions
  wait_for_app_healthy "$DATABASE_APP"
  az containerapp revision set-mode \
    --name "$DATABASE_APP" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --mode single --only-show-errors --output none
fi
DATABASE_FQDN="$(az containerapp show --name "$DATABASE_APP" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv --only-show-errors)"
[[ -n "$DATABASE_FQDN" ]] || die "Azure did not return an internal database FQDN."
DATABASE_URL="https://$DATABASE_FQDN"

COMMON_AI_OFF=(
  "AI_ENABLED=false"
  "MCP_ENABLED=false"
  "RAG_ENABLED=false"
  "MULTI_AGENT_ENABLED=false"
  "FLASK_DEBUG=0"
)
case "$STUDENT" in
  1) BACKEND_ENV=("PORT=$BACKEND_PORT" "HOST=0.0.0.0" "DATABASE_URL=$DATABASE_URL" "AUTH_ENABLED=0") ;;
  2) BACKEND_ENV=("PORT=$BACKEND_PORT" "DATABASE_API_URL=$DATABASE_URL" "USE_EXTERNAL_STUBS=true") ;;
  3) BACKEND_ENV=("PORT=$BACKEND_PORT" "DATABASE_URL=$DATABASE_URL" "AGENT_ENABLED=false") ;;
  4) BACKEND_ENV=("PORT=$BACKEND_PORT" "DATABASE_URL=$DATABASE_URL" "AUTH_ENABLED=0") ;;
  5) BACKEND_ENV=("BACKEND_PORT=$BACKEND_PORT" "DATABASE_SERVICE_URL=$DATABASE_URL") ;;
esac

BACKEND_HEALTH_PATH=/health
[[ "$STUDENT" == "5" ]] && BACKEND_HEALTH_PATH=/live
upsert_app "$BACKEND_APP" backend "$BACKEND_PORT" internal "$BACKEND_HEALTH_PATH" "" \
  "${BACKEND_ENV[@]}" "${COMMON_AI_OFF[@]}"
if [[ "$STUDENT" == "5" ]]; then
  wait_for_app_healthy "$BACKEND_APP"
fi
BACKEND_FQDN="$(az containerapp show --name "$BACKEND_APP" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv --only-show-errors)"
[[ -n "$BACKEND_FQDN" ]] || die "Azure did not return an internal backend FQDN."
BACKEND_URL="https://$BACKEND_FQDN"

case "$STUDENT" in
  1) FRONTEND_ENV=("PORT=$FRONTEND_PORT" "HOST=0.0.0.0" "FLASK_DEBUG=0" "BACKEND_URL=$BACKEND_URL") ;;
  2) FRONTEND_ENV=("BACKEND_API_URL=$BACKEND_URL" "FLASK_DEBUG=0") ;;
  3) FRONTEND_ENV=("PORT=$FRONTEND_PORT" "FLASK_DEBUG=0" "BACKEND_API_URL=$BACKEND_URL") ;;
  4) FRONTEND_ENV=("PORT=$FRONTEND_PORT" "HOST=0.0.0.0" "FLASK_DEBUG=0" "BACKEND_URL=$BACKEND_URL") ;;
  5) FRONTEND_ENV=("FRONTEND_PORT=$FRONTEND_PORT" "STAFF_SHIFT_API_URL=$BACKEND_URL") ;;
esac

FRONTEND_HEALTH=/health
[[ "$STUDENT" == "1" ]] && FRONTEND_HEALTH=/
[[ "$STUDENT" == "5" ]] && FRONTEND_HEALTH=/live
upsert_app "$FRONTEND_APP" frontend "$FRONTEND_PORT" external "$FRONTEND_HEALTH" "" \
  "${FRONTEND_ENV[@]}"
FRONTEND_FQDN="$(az containerapp show --name "$FRONTEND_APP" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv --only-show-errors)"
[[ -n "$FRONTEND_FQDN" ]] || die "Azure did not return an external frontend FQDN."
FRONTEND_URL="https://$FRONTEND_FQDN"

EVIDENCE_DIR="$REPOSITORY_ROOT/.local-r2/cloud"
mkdir -p "$EVIDENCE_DIR"
EVIDENCE_FILE="$EVIDENCE_DIR/student${STUDENT}-${IMAGE_TAG}-deployment.json"
python3 - "$EVIDENCE_FILE" <<PY
import json
import sys

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump({
        "student": $STUDENT,
        "image_tag": "$IMAGE_TAG",
        "resource_group": "$HOMS_AZURE_RESOURCE_GROUP",
        "container_apps_environment": "$HOMS_CONTAINERAPPS_ENVIRONMENT",
        "apps": {
            "frontend": {"name": "$FRONTEND_APP", "url": "$FRONTEND_URL"},
            "backend": {"name": "$BACKEND_APP", "url": "$BACKEND_URL"},
            "database": {"name": "$DATABASE_APP", "url": "$DATABASE_URL"},
        },
        "images": {
            "frontend": "$FRONTEND_IMAGE",
            "backend": "$BACKEND_IMAGE",
            "database": "$DATABASE_IMAGE",
        },
        "baseline": {
            "ai_enabled": False,
            "mcp_enabled": False,
            "rag_enabled": False,
            "multi_agent_enabled": False,
        },
        "database": {
            "storage_name": "$STORAGE_NAME",
            "mount_path": "/data",
            "min_replicas": 1,
            "max_replicas": 1,
        },
    }, handle, indent=2)
    handle.write("\n")
PY

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'frontend_url=%s\n' "$FRONTEND_URL" >>"$GITHUB_OUTPUT"
  printf 'frontend_app=%s\n' "$FRONTEND_APP" >>"$GITHUB_OUTPUT"
  printf 'backend_app=%s\n' "$BACKEND_APP" >>"$GITHUB_OUTPUT"
  printf 'database_app=%s\n' "$DATABASE_APP" >>"$GITHUB_OUTPUT"
  printf 'evidence_file=%s\n' "$EVIDENCE_FILE" >>"$GITHUB_OUTPUT"
fi

log "Student $STUDENT deployment completed without touching other student apps."
log "Frontend: $FRONTEND_URL"
log "Evidence: $EVIDENCE_FILE"

