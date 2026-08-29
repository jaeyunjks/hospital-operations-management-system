# student-1

Independently owned feature microservice set for the Hospital Operations
Management System. **Scaffold only — no feature logic implemented yet.**

- Owner: **Jesse Keyser** · Student 1 · HOMS Group 10
- Feature area: 

## Layout

| Path        | Purpose                                                    |
|-------------|------------------------------------------------------------|
| `frontend/` | HTMX + HTML/CSS/JS frontend for this feature               |
| `backend/`  | Flask REST API (CRUD + Agentic AI integration)             |
| `database/` | SQLite database, seed/migration scripts                    |
| `tests/`    | Tests for this microservice                                |
| `Dockerfile`| Container build for this microservice (placeholder)        |

## Not yet implemented

CRUD endpoints, database schema, authentication, and AI workflows are
intentionally out of scope for the current repository-initialisation task.

## Naming and Organisational Structure

The student-1 feature area is structured as follows:

<pre>
<code>
    ├── Dockerfile
    ├── README.md
    ├── <b>backend</b> <i># Backend microservice</i>
    │   ├── Dockerfile
    │   ├── README.md
    │   ├── app.py <i># Composition for backend / API routes, error responses, and healthchecks</i>
    │   ├── auth.py <i># Manages role-based authorisation for backend / API</i>
    │   ├── config.py <i># Configuration for backend / API services</i>
    │   ├── requirements.txt 
    │   ├── responses.py <i># Shared standardised response helper</i>
    │   ├── <b>routes</b>
    │   │   ├── __init__.py
    │   │   └── ai_endpoints.py <i># AI-assisted endpoints for AI features</i>
    │   ├── <b>services</b>
    │   │   ├── __init__.py
    │   │   └── ollama_client.py <i># Client for the local Ollama LLM</i>
    │   └── validation.py # Input validation helper
    ├── <b>database</b>> <i># Database microservice</i>
    │   ├── Dockerfile
    │   ├── README.md 
    │   ├── app.py <i># Composition for database services, including resources, CRUD routes, and views</i>
    │   ├── database.py <i># SQLite access helper</i>
    │   ├── init_database.py <i># Creates and populates the Patient & Admissions database</i>
    │   ├── requirements.txt
    │   ├── schema.sql <i># Patient & Admissions database schema</i>
    │   └── seed_data.sql <i># Patient & Admissions seed data</i>
    ├── <b>frontend</b> <i># Frontend microservice</i>
    │   ├── Dockerfile
    │   ├── README.md
    │   ├── app.py <i># Renders pages, calls backend API</i>
    │   ├── <b>static</b> 
    │   └── <b>templates</b>
    └── <b>tests</b>
</code>
</pre>