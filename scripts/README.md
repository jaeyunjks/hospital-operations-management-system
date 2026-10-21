# Scripts

Helper scripts for local development, testing, and deployment workflows.

## Student 5 Release 2 local stack

From the repository root, start the complete Student 5 R2 development stack:

```bash
./scripts/start-student5-r2.sh
```

The launcher reuses or starts the installed Ollama runtime, starts the shared
host-local RAG, MCP, and Multi-Agent servers, and starts only the three Student
5 Compose services with RAG and MCP enabled. Runtime PID and log files live in
`.local-r2/` and are ignored by Git through the repository's `*.pid` and
`*.log` rules.

Stop only the Student 5 containers and host processes owned by the launcher:

```bash
./scripts/stop-student5-r2.sh
```

The stop command retains Compose containers, the Student 5 database volume,
RAG index, workflow audit history, and logs.

| Directory  | Purpose                                             |
|------------|-----------------------------------------------------|
| `build/`   | Build helpers (images / assets)                     |
| `test/`    | Test helpers (run per-service or integrated tests)  |
| `deploy/`  | Deployment helpers (Azure Container Apps, later)    |

The remaining subdirectories are placeholders for future shared helpers.
