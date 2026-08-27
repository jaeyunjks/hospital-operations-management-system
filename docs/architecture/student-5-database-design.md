# Student 5 Database Design

## Feature

Staff & Shift Management

## Purpose

The database microservice stores operational workforce data required for staff scheduling, shift management, and staff assignment.

## Entities

### Staff

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



### Shift

Stores planned hospital shifts.

Attributes:

- shift_id
- department
- shift_date
- start_time
- end_time
- required_role
- required_staff_count
- shift_status
- notes


### Shift Assignment

Stores staff allocation to shifts.

Attributes:

- assignment_id
- shift_id
- staff_id
- assignment_status
- approved_by
- approved_at
- created_at
- updated_at

#### Known discrepancy: `assigned_at`

The project registration schema lists `assigned_at` on this table. It is **not
implemented**. `created_at` records when the row was written and `approved_at`
records Staff Manager approval, but neither is the same business event as *when
the staff member was allocated to the shift* — an allocation can be recorded
before, and independently of, approval.

Status: **confirmed gap, deliberately deferred.** No consumer needs it yet
(the Staff Directory does not read assignment data), so adding a column, seed
values, repository/service/API plumbing and tests now would be churn on an
untested path. It should be corrected as the first task of the Shift Planner
iteration, where assignment timestamps are actually surfaced.

`approved_by` / `approved_at` are retained — they support the existing Staff
Manager approval workflow and are not substitutes for `assigned_at`.


### Staff Weekly Availability

Recurring weekly availability, owned by HOMS. Added in Staff Directory
iteration 4.

Attributes:

- availability_id
- staff_id (FK -> staff.staff_id, ON DELETE CASCADE)
- day_of_week (0 = Monday ... 6 = Sunday)
- start_time / end_time ('HH:MM'; end < start denotes an overnight period)
- notes
- created_at / updated_at

Relationship: `STAFF 1:M STAFF_WEEKLY_AVAILABILITY`.

**Sparse by design.** A row *is* an available period; the absence of a row
means not available. No `Unavailable` rows are stored, because Release 0 has
no requirement to distinguish "explicitly blocked" from "no availability".

**Overlap** is validated in the service layer, not by a CHECK constraint:
SQLite cannot express a cross-row interval rule. Validation maps each period
onto absolute minutes in a seven-day week, so overnight periods and the
Sunday-to-Monday wrap are compared correctly.

### Staff Unavailability Request

Employee-submitted requests to be unavailable for a date range. Added in the
Scenario C workflow.

Attributes:

- request_id (Primary Key)
- staff_id (FK -> staff.staff_id, ON DELETE CASCADE)
- start_date / end_date ('YYYY-MM-DD', **inclusive at both ends**)
- reason (required) / notes (optional)
- request_status ('Pending' | 'Approved' | 'Rejected' | 'Cancelled')
- reviewed_by / reviewed_at
- created_at / updated_at

Relationship: `STAFF 1:M STAFF_UNAVAILABILITY_REQUEST`.

**One-way lifecycle.** `Pending` is the only non-terminal state:

```
Pending -> Approved   (manager decision)
Pending -> Rejected   (manager decision)
Pending -> Cancelled  (employee withdraws their own request)
```

`Approved`, `Rejected` and `Cancelled` are terminal. There is no route back
to `Pending`, and no route between terminal states — a decision, once
recorded, stands. Attempting any other transition is a 409.

**`reviewed_by` / `reviewed_at` are NULL for `Pending` and `Cancelled`.**
A pending request has not been decided, and withdrawing is the employee's own
act rather than a management decision, so neither may carry reviewer
metadata. `reviewed_by` is free TEXT: Release 0 has no authenticated user to
key a foreign key against, and inventing one would misrepresent what the
value is.

**Overlap** is enforced in the service layer, not by a CHECK constraint —
SQLite cannot express a cross-row rule. A new request is rejected when it
overlaps an existing `Pending` or `Approved` request for the same employee.
`Rejected` and `Cancelled` requests never block, so a period that was
declined can be requested again.

**Only `Approved` requests affect scheduling.** An approved request covering
a shift's date makes that employee ineligible for it. `Pending`, `Rejected`
and `Cancelled` requests have no effect on assignment whatsoever — a request
that has not been granted must not quietly change the roster.

#### What approval deliberately does NOT do

Approval records a decision and nothing else. It does not:

- unassign the employee from shifts they are already on;
- change `staff.availability_status`;
- create, cancel, or modify any `shift` or `shift_assignment` row.

Shifts that fall inside an approved period are **derived on read** by
intersecting the request's dates with live assignments, and are never stored
against the request. A stored conflict list would drift the moment the roster
changed; a derived one cannot. Acting on those shifts stays an explicit,
separate decision by the Staff Manager, reached through the existing Shift
Planner.

This is a scheduling record, not an HR leave record. HOMS holds no leave
balances, entitlements, accruals, certificates, or payroll fields, and
approving a request has no effect on any of them.


## Data ownership layering

Four distinct concepts, deliberately kept separate:

| Concept | Owner | Stored in |
|---------|-------|-----------|
| Employee reference data (name, role, department, specialisation, employment status) | External HR system | `staff` (read-only mirror in HOMS) |
| Current operational scheduling status | HOMS | `staff.availability_status` |
| Recurring weekly availability | HOMS | `staff_weekly_availability` |
| Actual allocation to shifts | HOMS | `shift_assignment` -> `shift` |
| Requested temporary unavailability | HOMS | `staff_unavailability_request` |

`availability_status` is the member's *current operational scheduling status*
(Available / Unavailable / On Leave). It is not a statement about a particular
day or time, and it does not mean "working right now".

Roster-derived states — "Rostered", and the mockup's "shift short" — are never
persisted. They are recomputed from `shift` + `shift_assignment` on each
render, so creating or cancelling an assignment never mutates the recurring
pattern. A member going On Leave likewise retains their weekly pattern; the
global status overrides scheduling eligibility without erasing the schedule.

## Relationships

Staff members can be assigned to multiple shifts.

A shift can contain multiple staff assignments.

Relationship:

Staff → Shift Assignment ← Shift

Staff → Staff Weekly Availability

Staff → Staff Unavailability Request

## Authorization (Release 0)

Role separation exists at two layers, and only one of them is a control:

| Layer | Mechanism | Is it a security control? |
|-------|-----------|---------------------------|
| Backend API | `backend/authorization.py` guards on every route | **Yes** — this is what enforces the rule |
| Frontend | Role-aware navigation, hidden controls, redirects | No — convenience and clarity only |

Identity reaches the backend as the `X-HOMS-Role` and `X-HOMS-Staff-Id`
headers. **These are unverified and self-asserted. This is not
authentication.** There is no credential, nothing is checked, and anyone able
to reach the backend directly can send whatever headers they like.

It is acceptable for Release 0 only because the permission *decision* is made
server-side: an employee who bypasses the frontend entirely still receives
403 from the API. Hiding a button changes what is easy to click; it does not
change what is permitted.

The shared HOMS authentication service replaces the header-reading function
in a later release. Every permission check downstream stays as written,
because none of them ask how the identity was established — only what it is.

