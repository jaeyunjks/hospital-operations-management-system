# HOMS Application Overview

## Purpose

The Hospital Operations Management System (HOMS) is a group-built microservices
application that supports day-to-day hospital operations. Five independently
owned features are integrated into one application:

| Owner | Feature |
|---|---|
| Student 1 | Patient & Admission Management |
| Student 2 | Clinical Staff Management |
| Student 3 | Pharmacy & Medication Inventory Management |
| Student 4 | Room & Bed Management |
| Student 5 | Staff & Shift Management |

## Microservice architecture

Every feature is split into three containerised microservices: a frontend, a
backend/API and a database service. The frontend only calls its own backend,
and the backend is the only component that calls its database service. Feature
ports follow one pattern: frontends use 3x00, backends use 5x00 and database
services use 6x00, where x is the student number (for example the pharmacy
backend runs on port 5300). Docker Compose deploys all fifteen feature
containers on one shared network.

## Local AI services

AI capabilities run locally on the developer machine and are not containerised.

- AI-Mode uses Ollama models (Llama, Qwen or DeepSeek) for advisory features.
- The shared MCP server (port 8000) exposes registered, read-only tools that
  feature backends call on behalf of their frontends.
- The shared RAG server (port 8100) answers questions using this knowledge base
  and returns source citations with a confidence category. When the knowledge
  base has no relevant context it returns an insufficient-context response
  instead of guessing.

Backend containers reach these host services through `host.docker.internal`.
In CI, MCP and RAG remain integrated but are disabled.
