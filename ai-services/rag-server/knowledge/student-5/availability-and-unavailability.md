# Availability and Unavailability

## Separate availability concepts

Staff & Shift Management keeps four concepts separate:

- `availability_status` is a person's current operational scheduling status;
- weekly availability is a recurring pattern of available periods;
- shift assignments are actual allocations to dated shifts; and
- unavailability requests are temporary requests for inclusive date ranges.

Changing one concept does not automatically rewrite the others.

## Recurring weekly availability

Weekly availability is sparse: each stored row is an available period, and the
absence of a row means no recurring availability is recorded for that time.
Days use 0 for Monday through 6 for Sunday. Start and end times use 24-hour
`HH:MM` values.

Updating weekly availability replaces the staff member's complete pattern; an
empty list clears it. Employees can read and replace their own pattern, while a
Staff Manager can read or replace any employee's pattern.

Zero-length and overlapping periods are rejected. An end time earlier than the
start time is a valid overnight period, including a Sunday-to-Monday wrap.
Adjacent periods are allowed. A weekly period matches a shift only when the
recorded periods cover the shift's whole time window.

## Weekly availability is advisory

Recurring weekly availability informs the candidate view but does not block an
assignment. A candidate outside their recurring pattern can still be eligible,
and the manager may roster them after reviewing the advisory note.

Replacing a weekly pattern does not change operational availability, create or
cancel a shift assignment, or submit an unavailability request.

## Operational availability

Operational availability is one of `Available`, `Unavailable`, or `On Leave`.
It is a current scheduling status, not a statement that the person is working
at this moment and not a date-specific leave record.

Only a Staff Manager can change operational availability. `Unavailable` and
`On Leave` block the candidate eligibility evaluation, but changing the status
does not unassign the person, cancel an assignment, create a replacement,
change the weekly pattern, or create an unavailability request. An active
assignment continues to count towards coverage until explicitly changed.

## Unavailability request lifecycle

An employee can submit their own unavailability request with an inclusive
start date, end date, and required reason. A new request starts as `Pending`.
A period cannot overlap another `Pending` or `Approved` request for the same
employee; `Rejected` and `Cancelled` requests do not reserve the period.

The allowed one-way transitions are:

- `Pending` to `Approved` by a Staff Manager;
- `Pending` to `Rejected` by a Staff Manager; or
- `Pending` to `Cancelled` by the employee who submitted it.

`Approved`, `Rejected`, and `Cancelled` are terminal states. Manager review
requires a reviewer name. Employee cancellation does not record reviewer
metadata.

## Effect of an approved request

Only an `Approved` request covering the shift date blocks candidate
eligibility. The start and end dates both count. `Pending`, `Rejected`, and
`Cancelled` requests do not block assignment.

Approval records the decision only. It does not change operational
availability, edit a weekly pattern, create or change a shift, or cancel an
existing assignment. Any existing assignments inside the approved date range
are derived from the current roster when the request is viewed; resolving them
remains a separate Staff Manager decision.
