#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/.local-r2"
COMPOSE_SERVICES=(student5-frontend student5-backend student5-database)

cd "${REPO_ROOT}"

say() {
  printf '[student5-r2] %s\n' "$*"
}

stop_managed_process() {
  local name="$1"
  local marker="$2"
  local pid_file="${RUNTIME_DIR}/${name}.pid"
  local pid=""
  local count=0

  [ -f "${pid_file}" ] || {
    say "${name}: not managed by this launcher"
    return 0
  }

  pid="$(tr -d '[:space:]' < "${pid_file}")"
  if [ -z "${pid}" ] || ! kill -0 "${pid}" 2>/dev/null; then
    rm -f "${pid_file}"
    say "${name}: removed stale PID file"
    return 0
  fi

  if ! ps -p "${pid}" -o command= 2>/dev/null | grep -F "${marker}" >/dev/null; then
    say "${name}: PID ${pid} no longer matches; leaving the process untouched"
    rm -f "${pid_file}"
    return 0
  fi

  say "Stopping ${name} (PID ${pid})"
  kill "${pid}"
  while kill -0 "${pid}" 2>/dev/null && [ "${count}" -lt 10 ]; do
    sleep 1
    count=$((count + 1))
  done
  if kill -0 "${pid}" 2>/dev/null; then
    say "${name}: did not stop within 10 seconds; leaving it for manual review"
    return 1
  fi
  rm -f "${pid_file}"
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  say "Stopping Student 5 containers (containers and database volume are retained)"
  docker compose stop "${COMPOSE_SERVICES[@]}"
else
  say "Docker is unavailable; skipping Student 5 containers"
fi

stop_managed_process multi-agent "ai-services/multi-agent-server/api.py"
stop_managed_process mcp "ai-services/mcp-server/server.py"
stop_managed_process rag "ai-services/rag-server/server.py"
stop_managed_process ollama "ollama serve"

say "Student 5 R2 launcher-managed services are stopped"
