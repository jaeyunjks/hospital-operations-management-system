# Student 5 – Release 1 Contribution Log

## Student

- **Student:** Yafie
- **Assigned Feature:** Staff & Shift Management
- **Release:** Release 1 – MCP, RAG & Intelligent Agent Integration

## Release 1 Feature Contribution

Extended the existing Staff & Shift Management feature from Release 0 while retaining its frontend, backend/API, database and AI Mode functionality.

Release 1 work included:

- Integrated the Staff & Shift frontend with the shared MCP server through the Student 5 backend/API.
- Added ward occupancy context to the Workforce Overview and Shift Planner.
- Integrated the `homs_ward_occupancy_status` MCP tool.
- Ensured ward occupancy data is accessed through the Student 4 Room & Bed API rather than directly accessing the Student 4 database.
- Added controlled MCP validation, timeout, unavailable-service and invalid-response handling.
- Added Staff & Shift RAG integration through the Student 5 backend/API.
- Added a Staff & Shift Guidance interface to the Shift Planner.
- Added four Staff & Shift knowledge documents for grounded retrieval.
- Displayed grounded RAG answers with citations and confidence categories.
- Added explicit insufficient-context handling for unsupported questions.
- Added Docker host connectivity configuration for the shared local MCP and RAG services.
- Updated Student 5 CI configuration so AI Mode, MCP and RAG are disabled during CI/CD execution.

## Shared / Group Contribution

Contributed to Release 1 integration and validation by:

- Supporting development and validation of the shared MCP integration.
- Integrating Student 5 with the shared MCP and RAG infrastructure.
- Validating cross-feature MCP access between Staff & Shift and Room & Bed.
- Contributing to Release 1 architecture, integration testing and evidence preparation.
- Supporting group-level Release 1 report and showcase preparation.

## Validation

Student 5 validation included:

- **827 Student 5 automated tests passed**
- Docker Compose configuration validation passed.
- Student 5 frontend, backend/API and database remained operational after the Release 1 extension.
- MCP integration verified through the Student 5 backend/API.
- RAG integration verified with grounded answers, citations and confidence.
- GitHub Actions Student 5 workflow completed successfully with AI, MCP and RAG disabled during CI.

## Relevant Release 1 Commits

| Commit | Contribution |
|---|---|
| `bc47361` | Integrated Student 5 with shared MCP |
| `e560051` | Added Student 5 MCP Docker/host configuration |
| `9e73532` | Updated CI with MCP disabled |
| `e243f0c` | Added MCP ward occupancy context to Shift Planner |
| `2ba3843` | Shift Planner UI improvements |
| `e08cc5e` | Added Student 5 RAG knowledge base |
| `04b6280` | Added Student 5 backend RAG integration |
| `289756f` | Added frontend RAG guidance interface |
| `e8e5781` | Added RAG Docker/host and CI configuration |

## Known Limitations

- Demo role headers are used for role-based access rather than production authentication.
- Shared MCP, RAG and local AI services must be started separately on the host machine.
- Ward occupancy provides current operational context rather than future occupancy forecasting.
- The RAG index must be rebuilt when knowledge documents are changed.