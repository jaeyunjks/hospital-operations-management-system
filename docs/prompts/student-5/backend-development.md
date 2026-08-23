# Student 5 Backend Development Prompt Artefact

## Prompt ID

S5-BE-001

## Date

2026-08-23

## Model / Tool

Claude Code

---

# Purpose

Implement the backend/API microservice for the Student 5 Staff & Shift Management feature.

The backend will provide REST APIs that allow the frontend microservice to manage staff information, shifts, assignments, and AI-assisted staffing functionality.

---

# Context

We are developing the Hospital Operations Management System for UTS Advanced Software Development (41026) Release 0.

Student 5 owns:

## Staff & Shift Management

The feature supports:

- Staff information management
- Staff availability management
- Shift scheduling
- Staff assignment
- Staffing coverage monitoring
- AI-assisted staff recommendations

The database microservice has already been implemented.

Database entities:

- Staff
- Shift
- Shift Assignment

Relationship:

Staff 1:M Shift Assignment M:1 Shift

---

# Existing Architecture

The system follows:

HTMX Frontend
        |
        v
Backend/API Microservice
        |
        v
Database Microservice
        |
        v
SQLite Database

The backend should act as the application service layer.

---

# Task Instructions

Implement the backend/API microservice inside:

student-5/backend/

The implementation should:

1. Create a Flask REST API service.
2. Provide endpoints for Staff & Shift Management.
3. Validate incoming requests.
4. Handle errors appropriately.
5. Communicate with the database microservice.
6. Maintain separation between services.

Required functionality:

## Staff

- Retrieve staff information
- Search staff
- Update staff availability


## Shift

- Create shifts
- Retrieve shifts
- Update shifts
- Delete shifts


## Assignment

- Assign staff to shifts
- Remove staff assignments
- Retrieve staffing coverage


## AI Preparation

Create backend structure that allows future AI integration:

- Staff recommendation
- Coverage analysis

Do not implement the actual LLM integration yet.

---

# Constraints

Do not:

- Create frontend files
- Modify database schema without justification
- Add authentication
- Access SQLite files directly from frontend
- Implement unrelated features

Follow the existing repository structure.

---

# Expected Output

Provide:

- Backend source files
- Flask application setup
- API route structure
- Service layer
- Database communication layer
- README documentation
- Testing instructions

---

# Validation Criteria

The backend should:

- Start successfully.
- Provide working REST endpoints.
- Correctly interact with database services.
- Handle invalid requests.
- Return appropriate responses.
- Follow the approved architecture.

---

# Outcome

(To be completed after AI execution)