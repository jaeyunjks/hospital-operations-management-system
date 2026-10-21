# Local security checks

The repository uses one root pre-commit configuration for Ruff security rules,
detect-secrets, and independent dependency audits for every Python service.
Generated Multi-Agent histories and agent-loop logs are excluded from the
secret baseline; tracked source, configuration, seed data, and normal evidence
remain in scope.

## Install and enable

```bash
python3 -m pip install pre-commit ruff detect-secrets pip-audit
pre-commit install
```

Use a project virtual environment or other isolated Python environment rather
than changing the system Python installation.

## Run all checks

```bash
pre-commit run --all-files
```

The hooks intentionally fail when a real Ruff or dependency finding exists.
Do not bypass a result by broadening an ignore or adding a vulnerability ID to
an allowlist without review.

## Run individual checks

```bash
ruff check --config ruff.toml .
git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline
./scripts/security/audit-requirements.sh
```

To refresh the secret baseline after reviewing repository changes:

```bash
detect-secrets scan \
  --exclude-files '^(reports/multi-agent/|docs/agent-logs/|\.local-r2/|student-5/(playwright-cli|\.playwright-cli|output/playwright)/)' \
  --baseline .secrets.baseline . > .secrets.baseline.new
```

Review the new file before replacing the baseline. Never baseline a real
credential. The agentic loop currently has no separate requirements file; its
standard-library implementation is still covered by Ruff and detect-secrets.

