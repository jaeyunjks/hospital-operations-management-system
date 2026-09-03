# Student 3 Frontend

This Flask scaffold serves the Pharmacy & Medication Inventory pages on port
3300. It loads the Student 3 common HOMS stylesheet through
`/static/css/main.css`.

The Dockerfile copies `shared/frontend/` into `/app/shared/frontend/`, so the
shared stylesheet is available in the container without changing Docker
Compose. Docker builds must use the repository root as their build context.

`BACKEND_API_URL` defaults to `http://localhost:5300` and is configuration for
later backend integration. The current page scaffold makes no new API calls.
