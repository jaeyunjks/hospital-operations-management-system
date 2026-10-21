#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/.local-r2"
PYTHON_BIN="${PYTHON_BIN:-python3}"
COMPOSE_SERVICES=(student5-database student5-backend student5-frontend)
STARTED_PIDS=""

cd "${REPO_ROOT}"
mkdir -p "${RUNTIME_DIR}"

say() {
  printf '[student5-r2] %s\n' "$*"
}

fail() {
  printf '[student5-r2] ERROR: %s\n' "$*" >&2
  trap - ERR
  cleanup_new_processes
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

port_open() {
  "${PYTHON_BIN}" - "$1" <<'PY'
import socket
import sys

with socket.socket() as connection:
    connection.settimeout(0.4)
    raise SystemExit(connection.connect_ex(("127.0.0.1", int(sys.argv[1]))) != 0)
PY
}

http_ready() {
  curl --noproxy '*' --fail --silent --show-error --max-time 3 "$1" >/dev/null 2>&1
}

mcp_ready() {
  "${PYTHON_BIN}" ai-services/mcp-server/cli.py tools >/dev/null 2>&1
}

wait_for() {
  local label="$1"
  local check="$2"
  local target="$3"
  local attempts="${4:-60}"
  local count=1

  while [ "${count}" -le "${attempts}" ]; do
    if "${check}" "${target}"; then
      say "${label} is ready"
      return 0
    fi
    sleep 1
    count=$((count + 1))
  done
  return 1
}

record_pid() {
  local name="$1"
  local pid="$2"
  printf '%s\n' "${pid}" > "${RUNTIME_DIR}/${name}.pid"
  STARTED_PIDS="${STARTED_PIDS} ${pid}"
}

show_log_tail() {
  local name="$1"
  local log_file="${RUNTIME_DIR}/${name}.log"
  if [ -f "${log_file}" ]; then
    printf '\n[student5-r2] Last lines from %s:\n' "${log_file}" >&2
    tail -n 20 "${log_file}" >&2 || true
  fi
}

cleanup_new_processes() {
  local pid
  for pid in ${STARTED_PIDS}; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
}

on_error() {
  local status=$?
  cleanup_new_processes
  printf '[student5-r2] Startup failed. Processes started by this run were stopped.\n' >&2
  exit "${status}"
}

trap on_error ERR

require_command "${PYTHON_BIN}"
require_command curl
require_command docker
require_command ollama

docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop and retry."
docker compose version >/dev/null 2>&1 || fail "Docker Compose is unavailable."

"${PYTHON_BIN}" - <<'PY' || fail "Local AI dependencies are missing. Install the requirements files under ai-services/{rag-server,mcp-server,multi-agent-server}."
import flask
import httpx
import mcp
import uvicorn
PY

# Reuse an existing Ollama process. If none is listening, start the installed
# runtime and remember ownership so the matching stop script can stop only it.
if http_ready "http://127.0.0.1:11434/api/tags"; then
  say "Ollama is already running"
elif port_open 11434; then
  fail "Port 11434 is occupied, but it is not a healthy Ollama API."
else
  say "Starting installed Ollama runtime"
  nohup ollama serve > "${RUNTIME_DIR}/ollama.log" 2>&1 &
  record_pid ollama "$!"
  if ! wait_for "Ollama" http_ready "http://127.0.0.1:11434/api/tags" 45; then
    show_log_tail ollama
    fail "Ollama did not become ready."
  fi
fi

# RAG's real index is local build output. Build it only when it is absent;
# normal repeated launches leave the existing index untouched.
if [ ! -s "ai-services/rag-server/.index/index.json" ]; then
  say "RAG index is missing; building it once with the repository ingest entrypoint"
  "${PYTHON_BIN}" ai-services/rag-server/ingest.py \
    > "${RUNTIME_DIR}/rag-ingest.log" 2>&1 || {
      show_log_tail rag-ingest
      fail "RAG index creation failed."
    }
fi

if http_ready "http://127.0.0.1:8100/health"; then
  say "RAG server is already running"
elif port_open 8100; then
  fail "Port 8100 is occupied, but the existing RAG server is not healthy."
else
  say "Starting shared RAG server"
  nohup env \
    HOMS_RAG_HOST=0.0.0.0 \
    HOMS_RAG_ALLOW_DOCKER_HOST=true \
    "${PYTHON_BIN}" ai-services/rag-server/server.py \
    > "${RUNTIME_DIR}/rag.log" 2>&1 &
  record_pid rag "$!"
  if ! wait_for "RAG server" http_ready "http://127.0.0.1:8100/health" 60; then
    show_log_tail rag
    fail "RAG server did not become healthy. Check that the required Ollama models are installed."
  fi
fi

if mcp_ready; then
  say "MCP server is already running"
elif port_open 8000; then
  fail "Port 8000 is occupied, but the existing MCP server did not answer tool discovery."
else
  say "Starting shared MCP server"
  nohup env \
    HOMS_MCP_HOST=0.0.0.0 \
    HOMS_MCP_ALLOW_DOCKER_HOST=true \
    HOMS_STUDENT5_API_URL=http://127.0.0.1:5500/api \
    "${PYTHON_BIN}" ai-services/mcp-server/server.py \
    > "${RUNTIME_DIR}/mcp.log" 2>&1 &
  record_pid mcp "$!"
  if ! wait_for "MCP server" mcp_ready unused 60; then
    show_log_tail mcp
    fail "MCP server did not become ready."
  fi
fi

if http_ready "http://127.0.0.1:8200/health"; then
  say "Multi-Agent server is already running"
elif port_open 8200; then
  fail "Port 8200 is occupied, but the existing Multi-Agent server is not healthy."
else
  say "Starting shared Multi-Agent server"
  nohup env \
    HOMS_MULTI_AGENT_HOST=0.0.0.0 \
    HOMS_MULTI_AGENT_ALLOW_DOCKER_HOST=true \
    "${PYTHON_BIN}" ai-services/multi-agent-server/api.py \
      --model-mode auto \
      --max-model-attempts 1 \
      --ollama-timeout 180 \
    > "${RUNTIME_DIR}/multi-agent.log" 2>&1 &
  record_pid multi-agent "$!"
  if ! wait_for "Multi-Agent server" http_ready "http://127.0.0.1:8200/health" 60; then
    show_log_tail multi-agent
    fail "Multi-Agent server did not become ready."
  fi
fi

say "Starting the targeted Student 5 Compose services"
export STUDENT5_AI_ENABLED=true
export STUDENT5_OLLAMA_URL=http://host.docker.internal:11434
export STUDENT5_RAG_ENABLED=true
export STUDENT5_RAG_SERVER_URL=http://host.docker.internal:8100
export STUDENT5_MCP_ENABLED=true
export STUDENT5_MCP_SERVER_URL=http://host.docker.internal:8000/mcp
export STUDENT5_MULTI_AGENT_BASE_URL=http://host.docker.internal:8200
export COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-plain}"

source_fingerprint="$(${PYTHON_BIN} - <<'PY'
import hashlib
from pathlib import Path

root = Path.cwd()
inputs = [
    root / "docker-compose.yml",
    root / "student-5/database",
    root / "student-5/backend",
    root / "student-5/frontend",
    root / "shared/frontend",
]
ignored_parts = {"__pycache__", ".pytest_cache", ".mypy_cache"}
ignored_suffixes = {".pyc", ".pyo", ".log", ".pid", ".db", ".sqlite", ".sqlite3"}
files = []
for item in inputs:
    if item.is_file():
        files.append(item)
        continue
    files.extend(
        path for path in item.rglob("*")
        if path.is_file()
        and not ignored_parts.intersection(path.parts)
        and path.suffix not in ignored_suffixes
    )

digest = hashlib.sha256()
for path in sorted(files):
    digest.update(path.relative_to(root).as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
PY
)"
fingerprint_file="${RUNTIME_DIR}/student5-compose.source.sha256"
stored_fingerprint=""
[ ! -f "${fingerprint_file}" ] || stored_fingerprint="$(tr -d '[:space:]' < "${fingerprint_file}")"
build_required=false
if [ "${source_fingerprint}" != "${stored_fingerprint}" ]; then
  build_required=true
fi
for service in "${COMPOSE_SERVICES[@]}"; do
  if [ -z "$(docker compose ps --all -q "${service}")" ]; then
    build_required=true
  fi
done

# Compose compares configuration and image state itself. --build incorporates
# local source changes, while an unchanged fingerprint avoids BuildKit emitting
# new image metadata and causing needless recreation on every repeated launch.
if [ "${build_required}" = true ]; then
  say "Student 5 source changed or a target container is missing; building images"
  docker compose up -d --build "${COMPOSE_SERVICES[@]}"
else
  say "Student 5 images are current; reusing unchanged containers"
  docker compose up -d "${COMPOSE_SERVICES[@]}"
fi

if ! wait_for "Student 5 database" http_ready "http://127.0.0.1:6500/health" 90; then
  docker compose logs --tail=40 student5-database >&2 || true
  fail "Student 5 database did not become healthy."
fi
if ! wait_for "Student 5 backend" http_ready "http://127.0.0.1:5500/health" 90; then
  docker compose logs --tail=40 student5-backend >&2 || true
  fail "Student 5 backend did not become healthy."
fi
if ! wait_for "Student 5 frontend" http_ready "http://127.0.0.1:3500/health" 90; then
  docker compose logs --tail=40 student5-frontend >&2 || true
  fail "Student 5 frontend did not become healthy."
fi

# Prove the container can reach every host-local service, not merely that each
# listener answers from the host. MCP has no ad-hoc health route, so a TCP
# connection is the appropriate startup probe; real tool discovery was already
# checked above through the official repository client.
docker compose exec -T student5-backend python - <<'PY' || fail "Student 5 backend cannot reach one or more host-local R2 services. Stop any loopback-only manual service and rerun the launcher."
import json
import os
import socket
import urllib.request

expected = {
    "RAG_ENABLED": "true",
    "RAG_SERVER_URL": "http://host.docker.internal:8100",
    "MCP_ENABLED": "true",
    "MCP_SERVER_URL": "http://host.docker.internal:8000/mcp",
    "MULTI_AGENT_BASE_URL": "http://host.docker.internal:8200",
    "DATABASE_SERVICE_URL": "http://student5-database:6500",
}
for name, value in expected.items():
    if os.environ.get(name) != value:
        raise SystemExit(f"unexpected {name} configuration")

for port, path in ((8100, "/health"), (8200, "/health")):
    request = urllib.request.Request(
        f"http://host.docker.internal:{port}{path}",
        headers={"Host": f"host.docker.internal:{port}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise SystemExit(f"host service on port {port} returned {response.status}")
        json.load(response)

with socket.create_connection(("host.docker.internal", 8000), timeout=5):
    pass
PY

trap - ERR
printf '%s\n' "${source_fingerprint}" > "${fingerprint_file}"

say "Student 5 R2 is ready"
printf '\n'
printf '  Frontend:    http://127.0.0.1:3500\n'
printf '  Backend:     http://127.0.0.1:5500/health\n'
printf '  Database:    http://127.0.0.1:6500/health\n'
printf '  RAG:         http://127.0.0.1:8100/health\n'
printf '  MCP:         http://127.0.0.1:8000/mcp\n'
printf '  Multi-Agent: http://127.0.0.1:8200/health\n'
printf '\nLogs and managed PIDs: %s\n' "${RUNTIME_DIR}"
printf 'Stop with: ./scripts/stop-student5-r2.sh\n'
