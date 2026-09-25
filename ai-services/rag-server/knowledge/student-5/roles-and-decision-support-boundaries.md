# Roles and Decision-Support Boundaries

## Staff Manager responsibilities

Staff Manager operations include workforce-wide staff reads, shift creation
and maintenance, coverage review, candidate review, assignment and
unassignment, operational availability updates, and review of employee
unavailability requests. Manager-only endpoints also expose AI staffing
support and the Room & Bed occupancy snapshot through MCP.

A Staff Manager may read or replace any employee's recurring weekly
availability. Staffing requirements, shift times, assignments, and any action
needed after an approved unavailability request remain explicit manager
decisions.

## Employee self-service

An Employee may read their own staff record, shifts, recurring weekly
availability, and unavailability requests. They may replace their own weekly
availability, submit their own unavailability request, and cancel their own
request while it is still `Pending`.

Employees cannot use workforce-wide staff, shift, coverage, candidate,
assignment, AI, MCP, or request-review operations. They cannot read or change
another employee's self-service data, review a request, or change operational
availability.

## Current authorization limitation

The backend enforces these permission decisions server-side. However, the
current identity is supplied through `X-HOMS-Role` and `X-HOMS-Staff-Id`
headers and is not authenticated. The headers are a development simulation,
not proof of identity. Frontend navigation and hidden controls are usability
features and are not security controls.

## AI Mode boundaries

AI Mode is optional and disabled by default. Deterministic eligibility and
coverage logic runs before any model call and remains authoritative.

For staff suggestions, the model may reorder an already eligible shortlist and
add short rationales. It cannot add an ineligible person, remove eligibility
rules, or assign anyone. The Staff Manager makes the final decision.

For coverage summaries, deterministic staffing figures remain the result. AI
narration is requested explicitly, uses aggregate scheduling data, and is
discarded when it contains unsupported figures or unsupported policy claims.
No weekly-hours policy is configured. If AI is disabled, unavailable, or
invalid, the deterministic result remains available.

## Room and Bed context through MCP

Room & Bed context is a read-only, current operational snapshot obtained by
the Student 5 backend through the shared MCP server and Student 4 API. Student
5 does not read the Student 4 database and does not infer a department-to-ward
mapping; an optional ward filter must use an exact Student 4 ward name.

The occupancy snapshot is informational. It does not change required staff,
create or edit shifts, assign employees, calculate future occupancy, or make a
staffing recommendation. MCP availability is independent of core Staff & Shift
operations.

## RAG guidance boundary

This Student 5 knowledge base provides documentation guidance about implemented
Staff & Shift behaviour. It does not contain live roster records and cannot say
which employee should be assigned now, calculate current coverage without
current Student 5 API data, decide how many staff a ward medically requires,
predict future ward occupancy, or provide patient-treatment guidance.

RAG answers are decision support only. Live operational questions must use the
authoritative Student 5 APIs, and staffing changes still require an explicit
Staff Manager action through the existing workflow.
