#!/usr/bin/env bash
# Terminal validation of the shared MCP server. Start server.py (and the Student 3
# backend on port 5300) first, then run from the repository root:
#   bash ai-services/mcp-server/validate.sh
# Exits non-zero if any command fails or returns an unexpected result.
PY=${PY:-python3}
URL=${HOMS_MCP_URL:-http://127.0.0.1:8000/mcp}
failures=0
run() {
  local shown=""; for a in "$@"; do case "$a" in *" "*|*"{"*) shown+=" '$a'";; *) shown+=" $a";; esac; done
  echo "\$ python3 ai-services/mcp-server/cli.py$shown"; "$PY" ai-services/mcp-server/cli.py "$@"; local code=$?
  echo "(exit $code)"; echo; [ $code -eq 0 ] || failures=$((failures + 1))
}
expect_http() {  # expect_http <status> <description> <curl args...>
  local want=$1 what=$2; shift 2
  local got; got=$(curl -s -o /dev/null -w '%{http_code}' "$@")
  echo "\$ curl $* -> HTTP $got ($what; expected $want)"
  [ "$got" = "$want" ] || failures=$((failures + 1)); echo
}
echo "# HOMS shared MCP server - terminal validation"
echo "# $(date -u +%Y-%m-%dT%H:%M:%SZ)  endpoint $URL"
echo
echo "## Registered tools"
run tools
echo "## Connectivity"
run call homs_echo '{"message": "terminal check"}' --expect ok --json
echo "## Pharmacy stock alerts (Student 3)"
run call homs_pharmacy_stock_alerts '{}' --expect ok
run call homs_pharmacy_stock_alerts '{"alert_type": "low_stock"}' --expect ok
run call homs_pharmacy_stock_alerts '{"alert_type": "expiring_soon"}' --expect ok
echo "## Pharmacy order alerts (Student 3)"
run call homs_pharmacy_order_alerts '{}' --expect ok
run call homs_pharmacy_order_alerts '{"alert_type": "pending_approval"}' --expect ok
run call homs_pharmacy_order_alerts '{"alert_type": "overdue"}' --expect ok --json
echo "## Tool boundaries: invalid input is rejected before any upstream call"
run call homs_pharmacy_stock_alerts '{"alert_type": "everything"}' --expect error
run call homs_pharmacy_order_alerts '{"alert_type": "overdue", "po_id": 4}' --expect error
run call homs_pharmacy_order_alerts '{"alert_type": 7}' --expect error
run call homs_echo '{"message": ""}' --expect error
run call homs_unknown_tool '{}' --expect error
echo "## Transport security"
expect_http 421 "unexpected Host header refused" -X POST "$URL" -H 'Host: evil.example:8000' \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{}'
echo "## Feature boundary: the Student 3 backend only forwards its own tools"
expect_http 403 "another feature's tool refused by the pharmacy backend" -X POST http://127.0.0.1:5300/api/mcp/call \
  -H 'Content-Type: application/json' -d '{"tool": "homs_ward_occupancy_status"}'
echo "# ${failures} failed check(s)"
exit $((failures > 0))
