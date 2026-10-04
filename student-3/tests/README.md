# Student 3 Tests

Each pharmacy service keeps its tests next to its own code, because the three
services are separate applications:

| Suite | Location | Tests |
|---|---|---|
| Database service | [`../database/tests/`](../database/tests/) | 4 |
| Backend/API | [`../backend/tests/`](../backend/tests/) | 48 |
| Frontend | [`../frontend/tests/`](../frontend/tests/) | 49 |

Run all of them from the repository root:

```bash
python3 -m pytest -q student-3
```

`student-3/conftest.py` lets the three suites share one run even though each
service has its own `app.py`. The tests use fakes for the database service,
Ollama, MCP and RAG, so nothing else needs to be running.
