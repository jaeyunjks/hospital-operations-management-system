# Student 3 Tests

Each pharmacy service keeps its unit tests next to its own code, because the
three services are separate applications:

| Suite | Location | Tests |
|---|---|---|
| Database service | [`../database/tests/`](../database/tests/) | 4 |
| Backend/API | [`../backend/tests/`](../backend/tests/) | 63 |
| Frontend | [`../frontend/tests/`](../frontend/tests/) | 61 |

Run all of them from the repository root:

```bash
python3 -m pytest -q student-3
```

`student-3/conftest.py` lets the three suites share one run even though each
service has its own `app.py`. The unit tests use fakes for the database service,
Ollama, MCP, RAG and the Multi-Agent Server, so nothing else needs to be running.

## Post-commit endpoint tests

[`test_endpoints_ci.py`](test_endpoints_ci.py) tests two backend/API endpoint
functions against a running backend container and a freshly seeded database:

| Endpoint | What is checked |
|---|---|
| `POST /api/stock/issue` | Stock is taken from the earliest-expiring batch first; an issue larger than the stock is refused and changes nothing; invalid requests return 400 |
| `POST /api/purchase-orders/{id}/approve` | Only a Pharmacy Manager can approve; a pending order becomes approved once and a second approval is refused (409); an unknown order returns 404 |

`student-3.yml` runs them after every push. To run them yourself, start a
backend with a fresh database (they change data), then:

```bash
STUDENT3_BACKEND_URL=http://localhost:5300 python3 -m unittest discover -v -s student-3/tests -p 'test_endpoints_ci.py'
```

Without `STUDENT3_BACKEND_URL` they are skipped, so `pytest student-3` stays
self-contained.
