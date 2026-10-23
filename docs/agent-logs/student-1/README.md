# Student 1 Agent Workflow Logs

This directory stores AI-assisted development workflow records.

Examples:

- Plan → Act → Observe → Adapt execution logs
- AI review outputs
- Improvement cycle records

## Release 2 reviews

| File name ends with | Loop mode | What the run shows |
|---|---|---|
| `-multi-agent-review` | `--mode multi-agent` | Two admission-scheduling workflows (one approved, one partially accepted) checked for stage order, evidence, Human Review and audit log. Workflow histories are in `docs/test-evidence/student-1/`. |
| `-testing-review` | `--mode testing` | Pre-commit scans, the JUnit report and the `student-1.yml` run (#44) checked together. Inputs are in `docs/test-evidence/student-1/`. |

```bash
python3 ai-services/agentic-loop/agentic_loop.py --mode multi-agent --student 1 \
  --workflow-id <workflow-id> --workflow-id <workflow-id>
```

```bash
python3 ai-services/agentic-loop/agentic_loop.py --mode testing --student 1 --students 1 \
  --precommit docs/test-evidence/student-1/pre-commit-all-files.txt \
  --junit student-1=docs/test-evidence/student-1/student-1-endpoints.xml \
  --run student-1=docs/test-evidence/student-1/student-1-run.json
```
