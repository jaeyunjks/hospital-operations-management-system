# Student 5 - Staff & Shift Management

Independent feature microservice set for the **Hospital Operations Management System**.

## Feature Owner

- Owner: **Yafie Farabi**
- Feature Area: **Staff & Shift Management**

---

# Feature Overview

The Staff & Shift Management module supports hospital workforce coordination by managing staff information, availability, shift scheduling, staff assignments, and AI-assisted staffing recommendations.

The feature focuses on improving operational efficiency by helping hospital administrators coordinate workforce allocation.

The module provides functionality for:

- Viewing staff information
- Managing staff availability
- Creating and managing shift schedules
- Assigning staff members to shifts
- Monitoring staffing coverage
- Receiving AI-assisted staff recommendations

---

# Release 0 Scope

This feature implements the Release 0 foundation required for the ASD project:

- Independent frontend microservice
- Backend/API microservice
- Database microservice
- CRUD operations
- AI-Mode integration using Ollama and approved open-source LLMs
- Docker containerisation
- GitHub Actions CI/CD integration

The following capabilities are reserved for future releases:

- Retrieval Augmented Generation (RAG)
- Model Context Protocol (MCP)
- Multi-agent workflows
- Advanced cloud deployment

---

# Microservice Architecture

The feature follows a microservice architecture:

```
Hospital Staff User
        |
        v
HTMX Frontend Microservice
        |
        | REST Communication
        v
Backend/API Microservice
        |
        | Database Communication
        v
Database Microservice


Backend/API Microservice
        |
        v
Ollama Runtime
        |
        v
Approved Open Source LLM
```

---

# Component Responsibilities

## Frontend Microservice

### Technology

- HTMX
- HTML
- CSS
- JavaScript

### Responsibilities

- Provide staff management interface
- Display staff information
- Display roster and shift information
- Support shift assignment interactions
- Display AI-generated recommendations

---

## Backend/API Microservice

### Technology

- Flask REST API

### Responsibilities

- Handle application business logic
- Process staff and shift operations
- Validate requests
- Coordinate communication between frontend, database, and AI services

The backend acts as the main service layer for Staff & Shift Management operations.

---

## Database Microservice

### Technology

- SQLite

### Responsibilities

- Manage staff and shift operational data
- Provide controlled database access
- Support CRUD operations

The backend communicates with the database through the database service rather than directly accessing database files.

---

## AI Integration

### Technology

- Ollama Runtime
- Approved Open Source LLM

### Purpose

The AI component provides decision-support capabilities for workforce management.

### Planned AI Functions

- Suggest suitable staff members for shifts
- Analyse staffing coverage
- Provide reasoning behind recommendations

AI output is used as a recommendation and requires human review before operational decisions.

---

# Functional Capabilities

## Staff Management

The module supports:

- View staff information
- Search and filter staff
- Update staff availability

---

## Shift Management

The module supports:

- Create shifts
- View upcoming shifts
- Update shift details
- Remove shifts

---

## Staff Assignment

The module supports:

- Assign staff to shifts
- Remove staff assignments
- Monitor staffing coverage requirements

---

## AI-Assisted Staffing

The module supports:

- Analyse shift requirements
- Recommend suitable available staff
- Explain recommendation reasoning

---

# Development Structure

```
student-5/
├── frontend/
│   └── HTMX frontend microservice
│
├── backend/
│   └── Flask API microservice
│
├── database/
│   └── SQLite database microservice
│
├── tests/
│   └── Feature testing
│
└── Dockerfile
```

---

# Relationship With External Systems

The Staff & Shift Management module receives relevant employee information from external HR systems.

The external HR system is responsible for maintaining authoritative employee records.

This feature focuses on operational workforce management, including:

- Staff availability
- Shift planning
- Workforce allocation
- Coverage management

---

# AI Development Approach

AI functionality follows the ASD Agentic AI approach:

Plan → Act → Observe → Adapt

The AI-assisted development process will maintain documentation of:

- Development prompts
- AI-assisted implementation decisions
- Review outputs
- Improvement cycles

---

# Testing Approach

The feature will include testing for:

## Functional Testing

- Staff retrieval
- Shift creation
- Shift updates
- Staff assignment operations

## Integration Testing

- Frontend communication with backend
- Backend communication with database service
- Backend communication with AI service

## Non-Functional Testing

Examples:

- Service availability
- API response validation
- Error handling

---

# Docker and CI/CD

The feature will be containerised as part of the integrated Release 0 application.

Components:

- Frontend container
- Backend/API container
- Database container

The feature will include:

- Dockerfile configuration
- GitHub Actions workflow
- Automated validation pipeline

---

# AI Evidence and Documentation

AI-related development artefacts will be maintained separately:

```
docs/
├── prompts/
│   └── student-5/
└── agent-logs/
    └── student-5/
```

These artefacts document:

- AI prompts used during development
- Context provided to AI agents
- Agent workflow execution records
- Development decisions

---

# Current Development Status

## Completed

- Feature ownership assigned
- Feature scope defined
- Microservice architecture planned
- Repository structure prepared

## Upcoming Development Tasks

1. Implement database microservice
2. Implement backend API service
3. Implement frontend interface
4. Integrate Ollama AI functionality
5. Add testing
6. Configure CI/CD workflow
7. Integrate with Docker Compose

---

# Release 0 Definition of Done

The feature will be considered complete when:

- Frontend microservice is operational
- Backend/API microservice is operational
- Database microservice is operational
- CRUD operations are implemented
- AI-Mode integration is demonstrated
- Docker containerisation is completed
- GitHub Actions workflow succeeds
- Testing evidence is collected
- Feature is integrated into the shared application