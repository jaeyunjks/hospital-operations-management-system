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

## Data ownership layering

Four distinct concepts, deliberately kept separate:

| Concept | Owner | Stored in |
|---------|-------|-----------|
| Employee reference data (name, role, department, specialisation, employment status) | External HR system | `staff` (read-only mirror in HOMS) |
| Current operational scheduling status | HOMS | `staff.availability_status` |
| Recurring weekly availability | HOMS | `staff_weekly_availability` |
| Actual allocation to shifts | HOMS | `shift_assignment` -> `shift` |

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

