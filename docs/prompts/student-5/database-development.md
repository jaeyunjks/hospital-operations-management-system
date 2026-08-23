# Student 5 Database Development Prompt Artefact

## Prompt ID

S5-DB-001

## Date

2026-08-23

## Model / Tool

Claude Code

## Status

Active development prompt

---

# Purpose

Create the database microservice for the Student 5 Staff & Shift Management feature.

The purpose of this prompt is to guide AI-assisted implementation while maintaining traceability between the database design, implementation decisions, and validation evidence.

---

# Context

We are developing the Hospital Operations Management System for UTS Advanced Software Development (41026) Release 0.

The application follows a microservices architecture where each student owns an independent feature consisting of:

- Frontend microservice
- Backend/API microservice
- Database microservice
- AI integration capability
- Docker containerisation
- GitHub Actions workflow

## Student 5 Feature

Feature name:

Staff & Shift Management

Purpose:

Manage hospital workforce scheduling, staff availability, shift creation, staff assignment, and AI-assisted staffing recommendations.

The database microservice owns only the data required by this feature.

---

# Database Design

The database uses a relational design consisting of three entities.

## STAFF

Stores hospital workforce information.

Attributes:

- staff_id (Primary Key)
- name
- role
- department
- specialisation
- availability_status
- employment_status
- notes


## SHIFT

Stores planned hospital shifts.

Attributes:

- shift_id (Primary Key)
- department
- shift_date
- start_time
- end_time
- required_role
- required_staff_count
- shift_status
- notes


## SHIFT_ASSIGNMENT

Resolves the many-to-many relationship between staff members and shifts.

Attributes:

- assignment_id (Primary Key)
- shift_id (Foreign Key)
- staff_id (Foreign Key)
- assignment_status
- approved_by
- approved_at


Relationship:

One staff member can have many shift assignments.

One shift can have many staff assignments.

---

# Task Instructions

Implement the Student 5 database microservice.

The implementation should:

1. Create the database structure based on the approved design.
2. Create database models/schema definitions.
3. Implement database connection handling.
4. Create seed data.
5. Populate each required table with realistic sample records.
6. Ensure CRUD operations can be supported by the backend service.
7. Maintain separation between the database microservice and other services.

Create the required files inside:

student-5/database/

Follow the existing repository structure and coding conventions.

---

# Constraints

Do not:

- Change the approved database design without explanation.
- Add unnecessary entities.
- Implement authentication.
- Implement frontend logic.
- Implement backend API routes.
- Access another student's database.
- Create direct cross-service database dependencies.

The database microservice must remain independently deployable.

The implementation must support the Release 0 requirements:

- CRUD functionality
- Docker containerisation
- Integration with backend/API microservice later
- Seeded database records for testing

---

# Expected Output

Provide:

1. Database implementation files.
2. Database schema/model definitions.
3. Seed script containing realistic test data.
4. README update explaining database setup.
5. Instructions for running and validating the database service.

---

# Validation Criteria

The implementation will be validated by checking:

## Database Structure

- All required tables exist.
- Primary keys and foreign keys are correctly implemented.
- Relationships match the approved ERD.

## Data Population

- STAFF contains at least 10 records.
- SHIFT contains at least 10 records.
- SHIFT_ASSIGNMENT contains at least 10 records.

## Functionality

- Database can initialise successfully.
- Seed script executes successfully.
- Data can be queried correctly.

## Architecture Compliance

- Database remains isolated as a microservice.
- No direct access from other feature databases.
- Implementation aligns with Release 0 requirements.

---

# Outcome

(To be completed after AI execution)

Implementation result:

Validation evidence:

Related agent log: