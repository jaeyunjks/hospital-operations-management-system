#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=azure-common.sh
source "$SCRIPT_DIR/azure-common.sh"

STUDENT=""
FRONTEND_URL=""
DRY_RUN=false

usage() {
  cat <<'EOF'
Validate one deployed HOMS student slice without destructive data changes.

Usage:
  ./scripts/deploy/deployment-testing.sh STUDENT [--frontend-url URL] [--dry-run]

The frontend is probed from the runner. Internal backend/database health and a
representative read operation are probed from within the deployed containers.
No create, update or delete request is sent.
EOF
}

while (($#)); do
  case "$1" in
    --frontend-url)
      (($# >= 2)) || die "--frontend-url requires a value."
      FRONTEND_URL="$2"
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
FRONTEND_APP="$(container_app_name "$STUDENT" frontend)"
BACKEND_APP="$(container_app_name "$STUDENT" backend)"
DATABASE_APP="$(container_app_name "$STUDENT" database)"

case "$STUDENT" in
  1) READ_PATH="/api/patients"; READ_HEADERS='{}' ;;
  2) READ_PATH="/api/clinical-records/"; READ_HEADERS='{"X-User-Role":"doctor"}' ;;
  3) READ_PATH="/api/medicines"; READ_HEADERS='{}' ;;
  4) READ_PATH="/api/rooms"; READ_HEADERS='{}' ;;
  5) READ_PATH="/api/shifts"; READ_HEADERS='{}' ;;
esac

if [[ "$DRY_RUN" == true ]]; then
  log "Dry run: no deployed endpoint will be contacted."
  log "Frontend app: $FRONTEND_APP (external)"
  log "Backend app: $BACKEND_APP (internal)"
  log "Database app: $DATABASE_APP (internal)"
  log "Representative safe read: $READ_PATH"
  log "Checks: frontend, backend health, database health, read-only API, AI-off flags, database storage/replicas."
  exit 0
fi

require_value AZURE_SUBSCRIPTION_ID
azure_account_check
select_subscription
require_command curl
require_command python3

for app in "$FRONTEND_APP" "$BACKEND_APP" "$DATABASE_APP"; do
  az containerapp show --name "$app" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --only-show-errors >/dev/null 2>&1 || die "Container App '$app' was not found."
done

if [[ -z "$FRONTEND_URL" ]]; then
  FRONTEND_FQDN="$(az containerapp show --name "$FRONTEND_APP" \
    --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
    --query properties.configuration.ingress.fqdn --output tsv --only-show-errors)"
  [[ -n "$FRONTEND_FQDN" ]] || die "Frontend FQDN is unavailable."
  FRONTEND_URL="https://$FRONTEND_FQDN"
fi
FRONTEND_URL="${FRONTEND_URL%/}"

BACKEND_FQDN="$(az containerapp show --name "$BACKEND_APP" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv --only-show-errors)"
DATABASE_FQDN="$(az containerapp show --name "$DATABASE_APP" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv --only-show-errors)"
BACKEND_URL="https://$BACKEND_FQDN"
DATABASE_URL="https://$DATABASE_FQDN"

log "Checking external frontend $FRONTEND_URL."
curl --fail --silent --show-error \
  --retry 12 --retry-all-errors --retry-delay 5 \
  --max-time 20 "$FRONTEND_URL/" >/dev/null

remote_http_probe() {
  local source_app="$1"
  local url="$2"
  local expected_status="$3"
  local method="${4:-GET}"
  local headers="${5:-}"
  [[ -n "$headers" ]] || headers='{}'
  local expected_text="${6:-}"
  local encoded
  encoded="$(python3 - "$url" "$expected_status" "$method" "$headers" "$expected_text" <<'PY'
import base64
import json
import sys

url, expected, method, headers, expected_text = sys.argv[1:]
script = f'''import urllib.error
import urllib.request
request = urllib.request.Request({url!r}, method={method!r}, headers={json.loads(headers)!r}, data=(b"{{}}" if {method!r} == "POST" else None))
try:
    response = urllib.request.urlopen(request, timeout=20)
    status = response.status
    body = response.read().decode("utf-8", errors="replace")
except urllib.error.HTTPError as error:
    status = error.code
    body = error.read().decode("utf-8", errors="replace")
assert status == {int(expected)}, (status, body[:300])
assert {expected_text!r} in body, body[:300]
print("HOMS_PROBE_OK", status, {url!r})
'''
print(base64.b64encode(script.encode()).decode())
PY
)"
  local output
  output="$(printf '%s\n%s\n' \
    "python -c \"import base64;exec(base64.b64decode('$encoded'))\"" \
    "exit" | az containerapp exec \
      --name "$source_app" \
      --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
      --command sh \
      --only-show-errors 2>&1)"
  grep -q 'HOMS_PROBE_OK' <<<"$output" || {
    printf '%s\n' "$output" >&2
    die "Internal HTTP probe failed for $url."
  }
}

if [[ "$STUDENT" == "5" ]]; then
  # `az containerapp exec` requires an interactive TTY and is unreliable on a
  # GitHub-hosted runner. Student 5's public /health deliberately traverses
  # frontend -> backend -> database, so validate that real dependency chain.
  log "Checking Student 5 frontend, backend and database through public health."
  HEALTH_JSON="$(curl --fail --silent --show-error \
    --retry 12 --retry-all-errors --retry-delay 5 \
    --max-time 20 "$FRONTEND_URL/health")"
  python3 - "$HEALTH_JSON" <<'PY'
import json
import sys

health = json.loads(sys.argv[1])
assert health.get("status") == "ok"
backend = health.get("backend_service") or {}
assert backend.get("status") == "ok"
backend_detail = backend.get("detail") or {}
assert backend_detail.get("status") == "ok"
database = backend_detail.get("database_service") or {}
assert database.get("status") == "ok"
database_detail = database.get("detail") or {}
assert database_detail.get("status") == "ok"
PY

  COOKIE_FILE="$(mktemp)"
  trap 'rm -f "$COOKIE_FILE"' EXIT
  log "Checking the manager Shift Planner read journey through the public frontend."
  ENTER_STATUS="$(curl --silent --show-error \
    --cookie "$COOKIE_FILE" --cookie-jar "$COOKIE_FILE" \
    --data 'role=Staff Manager' --request POST \
    --output /dev/null --write-out '%{http_code}' \
    "$FRONTEND_URL/demo/enter")"
  [[ "$ENTER_STATUS" == "302" ]] || die \
    "Student 5 manager demo entry returned HTTP $ENTER_STATUS."
  SHIFT_PAGE="$(curl --fail --silent --show-error \
    --cookie "$COOKIE_FILE" --max-time 20 "$FRONTEND_URL/shifts")"
  grep -q 'Shift planner' <<<"$SHIFT_PAGE" || die \
    "Student 5 Shift Planner did not render through the deployed stack."
elif [[ "$STUDENT" == "2" ]]; then
  # `az containerapp exec` needs a TTY and fails on GitHub-hosted runners.
  # The Student 2 dashboard is rendered from a live backend read, which in turn
  # reads the database, so a 200 without a reachability error validates the
  # frontend -> backend -> database chain on Azure Files.
  log "Checking the Student 2 dashboard renders through the deployed stack."
  DASHBOARD_PAGE="$(curl --fail --silent --show-error \
    --retry 12 --retry-all-errors --retry-delay 5 \
    --max-time 30 "$FRONTEND_URL/")"
  grep -q 'Clinical Staff Management' <<<"$DASHBOARD_PAGE" || die \
    "Student 2 dashboard did not render through the deployed stack."
  if grep -q 'Could not reach' <<<"$DASHBOARD_PAGE"; then
    die "Student 2 dashboard rendered but could not reach the backend or database."
  fi
elif [[ "$STUDENT" == "4" ]]; then
  # `az containerapp exec` needs a TTY and fails on GitHub-hosted runners.
  # Student 4's public /health asks the backend for its own /health, which in
  # turn reads record counts from the database, so one public call validates
  # the frontend -> backend -> database chain on Azure Files.
  log "Checking Student 4 frontend, backend and database through public health."
  HEALTH_JSON="$(curl --fail --silent --show-error \
    --retry 12 --retry-all-errors --retry-delay 5 \
    --max-time 30 "$FRONTEND_URL/health")"
  python3 - "$HEALTH_JSON" <<'PY'
import json
import sys

health = json.loads(sys.argv[1])
assert health.get("error") is None, health.get("error")
backend = health.get("backend") or {}
database = backend.get("database") or {}
assert database.get("ok") is True, database
records = (database.get("detail") or {}).get("records") or {}
assert records.get("beds", 0) >= 10, records
PY

  log "Checking the Student 4 bed availability page renders through the deployed stack."
  AVAILABILITY_PAGE="$(curl --fail --silent --show-error \
    --retry 6 --retry-all-errors --retry-delay 5 \
    --max-time 30 "$FRONTEND_URL/")"
  grep -q 'Bed availability' <<<"$AVAILABILITY_PAGE" || die \
    "Student 4 bed availability page did not render through the deployed stack."
elif [[ "$STUDENT" == "1" ]]; then
  # `az containerapp exec` needs a TTY and fails on GitHub-hosted runners. The
  # Student 1 census page is rendered from live backend and database reads; its
  # seed fallback reads /database/schema.sql, which is absent from the frontend
  # image, so a 200 here means the frontend -> backend -> database chain works.
  log "Checking the Student 1 census page renders through the deployed stack."
  CENSUS_PAGE="$(curl --fail --silent --show-error \
    --retry 12 --retry-all-errors --retry-delay 5 \
    --max-time 30 "$FRONTEND_URL/")"
  grep -qi 'census' <<<"$CENSUS_PAGE" || die \
    "Student 1 census page did not render through the deployed stack."
else
  log "Checking internal backend health from the frontend container."
  remote_http_probe "$FRONTEND_APP" "$BACKEND_URL/health" 200 GET '{}'

  log "Checking internal database health from the backend container."
  remote_http_probe "$BACKEND_APP" "$DATABASE_URL/health" 200 GET '{}'

  log "Checking a representative read-only backend operation ($READ_PATH)."
  remote_http_probe "$FRONTEND_APP" "$BACKEND_URL$READ_PATH" 200 GET "$READ_HEADERS"
fi

BACKEND_ENV_JSON="$(az containerapp show --name "$BACKEND_APP" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query properties.template.containers[0].env --output json --only-show-errors)"
python3 - "$BACKEND_ENV_JSON" <<'PY'
import json
import sys

values = {item["name"]: item.get("value") for item in json.loads(sys.argv[1])}
for name in ("AI_ENABLED", "MCP_ENABLED", "RAG_ENABLED", "MULTI_AGENT_ENABLED"):
    assert values.get(name) == "false", f"{name} is not explicitly false"
PY

DATABASE_CONFIG_JSON="$(az containerapp show --name "$DATABASE_APP" \
  --resource-group "$HOMS_AZURE_RESOURCE_GROUP" \
  --query '{scale:properties.template.scale,volumes:properties.template.volumes,mounts:properties.template.containers[0].volumeMounts}' \
  --output json --only-show-errors)"
python3 - "$DATABASE_CONFIG_JSON" <<'PY'
import json
import sys

config = json.loads(sys.argv[1])
assert config["scale"]["minReplicas"] == 1
assert config["scale"]["maxReplicas"] == 1
assert any(volume.get("storageType") == "AzureFile" for volume in config["volumes"])
assert any(mount.get("mountPath") == "/data" for mount in config["mounts"])
PY

if [[ "$STUDENT" == "5" ]]; then
  log "Proving the Student 5 Multi-Agent workflow is blocked in the cloud baseline."
  RECOVERY_RESPONSE="$(curl --fail --silent --show-error \
    --cookie "$COOKIE_FILE" --request POST --max-time 20 \
    "$FRONTEND_URL/partials/shifts/11/recovery-workflow")"
  grep -q 'Multi-Agent service unavailable' <<<"$RECOVERY_RESPONSE" || die \
    "Student 5 cloud baseline did not visibly block Multi-Agent recovery."
  grep -q 'No roster changes have been made' <<<"$RECOVERY_RESPONSE" || die \
    "Student 5 cloud baseline did not confirm mutation safety."
fi

log "Student $STUDENT cloud validation passed; no mutating endpoint was called."

