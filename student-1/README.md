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

├── Dockerfile
├── README.md
├── **backend** # Backend microservice
│   ├── Dockerfile
│   ├── README.md
│   ├── app.py # Composition for backend / API routes, error responses, and healthchecks
│   ├── auth.py # Manages role-based authorisation for backend / API
│   ├── config.py # Configuration for backend / API services
│   ├── requirements.txt 
│   ├── responses.py # Shared standardised response helper
│   ├── **routes**
│   │   ├── __init__.py
│   │   └── ai_endpoints.py # AI-assisted endpoints for AI features
│   ├── **services**
│   │   ├── __init__.py
│   │   └── ollama_client.py # Client for the local Ollama LLM
│   └── validation.py # Input validation helper
├── **database** # Database microservice
│   ├── Dockerfile
│   ├── README.md 
│   ├── app.py # Composition for database services, including resources, CRUD routes, and views
│   ├── database.py # SQLite access helper
│   ├── init_database.py # Creates and populates the Patient & Admissions database
│   ├── requirements.txt
│   ├── schema.sql # Patient & Admissions database schema
│   └── seed_data.sql # Patient & Admissions seed data
├── **frontend** # Frontend microservice
│   ├── Dockerfile
│   ├── README.md
│   ├── app.py # Renders pages, calls backend API
│   ├── **static** 
│   └── **templates**
└── **tests**