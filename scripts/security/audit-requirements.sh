#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if ! command -v pip-audit >/dev/null 2>&1; then
  echo "[pip-audit] pip-audit is not installed." >&2
  echo "[pip-audit] Install it with: python3 -m pip install pip-audit" >&2
  exit 2
fi

cd "${REPO_ROOT}"

requirements="$({
  find student-1 student-2 student-3 student-4 student-5 ai-services \
    -type f -name 'requirements*.txt' -print
} | LC_ALL=C sort)"

if [ -z "${requirements}" ]; then
  echo "[pip-audit] No service requirements files were found." >&2
  exit 2
fi

overall_status=0
file_count=0

while IFS= read -r requirement_file; do
  [ -n "${requirement_file}" ] || continue
  file_count=$((file_count + 1))
  printf '\n[pip-audit] Auditing %s\n' "${requirement_file}"
  if ! pip-audit --strict --progress-spinner off --requirement "${requirement_file}"; then
    overall_status=1
  fi
done <<EOF
${requirements}
EOF

printf '\n[pip-audit] Audited %s independent requirements files.\n' "${file_count}"
if [ "${overall_status}" -ne 0 ]; then
  echo "[pip-audit] One or more services have dependency findings or could not be audited." >&2
fi
exit "${overall_status}"

