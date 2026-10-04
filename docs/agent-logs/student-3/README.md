# Student 3 Agent Workflow Logs

Captured outputs of the shared agentic loop
(`ai-services/agentic-loop/agentic_loop.py`) run for the pharmacy feature.
Every run writes a readable `.md` file and a matching `.json` file with the
same name.

| File name ends with | Loop mode | Release | What the run shows |
|---|---|---|---|
| `-agentic-loop` | `command` (default) | 0 | Plan → Act → Observe → Adapt around a validation command in `student-3/` |
| `-grounded-loop` | `--mode mcp` | 1 | The model answers a pharmacy question using shared MCP tools; the answer is checked against the tool results |
| `-rag-validation` | `--mode rag` | 1 | Answerable and out-of-scope questions sent to the shared RAG server; citations, confidence and insufficient-context responses are checked |

File names start with the UTC time of the run (for example `20261001T054436…`
is 1 October 2026, 05:44 UTC).

To produce new logs, with Ollama and the MCP and RAG servers running, from the
repository root:

```bash
python3 ai-services/agentic-loop/agentic_loop.py --mode mcp --student 3 --question "Which medicines are at or below their reorder level?" --tool homs_pharmacy_stock_alerts
```

```bash
python3 ai-services/agentic-loop/agentic_loop.py --mode rag --student 3
```
