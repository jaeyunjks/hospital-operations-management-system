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


## Relationships

Staff members can be assigned to multiple shifts.

A shift can contain multiple staff assignments.

Relationship:

Staff → Shift Assignment ← Shift

