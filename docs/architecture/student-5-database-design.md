# Student 5 Database Design

## Feature

Staff & Shift Management

## Purpose

The database microservice stores operational workforce data required for staff scheduling, shift management, and staff assignment.

## Entities

### Staff

Stores hospital workforce information.

Attributes:

- staff_id
- name
- role
- department
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


## Relationships

Staff members can be assigned to multiple shifts.

A shift can contain multiple staff assignments.

Relationship:

Staff → Shift Assignment ← Shift

