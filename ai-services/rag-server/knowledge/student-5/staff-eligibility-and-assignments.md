# Staff Eligibility and Assignments

## Why an employee may be ineligible for a shift

The manager-only candidate flow evaluates staff against a specific shift. The
candidate endpoint first loads staff who hold the shift's required role. It
returns eligible and blocked candidates with a `blocked_reason`; the AI
suggestion flow uses the same evaluation and keeps only eligible candidates.

The deterministic blockers are:

- already assigned to the target shift;
- operational availability status `Unavailable` or `On Leave`;
- an `Approved` unavailability request covering the shift date;
- a role that does not match the shift's required role; and
- an active assignment whose time overlaps the target shift.

Because the candidate endpoint prefilters by required role, a role-mismatched
person is normally absent from that endpoint rather than displayed with a role
mismatch. The underlying evaluator still treats a mismatch as ineligible.

## Shift conflicts and inactive assignments

Two shifts conflict when their dated time ranges share any minute. Overnight
shifts continue into the following day for this comparison. Back-to-back shifts
that only touch at an end and start time do not conflict.

Assignments with status `Cancelled` or `Declined` are inactive. They do not
count as an overlap and do not count towards coverage. Other assignment
statuses remain active for these calculations.

## Advisory and contextual information

Recurring weekly availability is advisory. A candidate receives
`weekly_ok: true` only when their recorded periods cover the whole shift, but
being outside the pattern does not make them ineligible.

Department, specialisation, and employment status are context and ordering
signals, not blockers. A person from another department can remain eligible.
The application calculates rostered hours as context, but no weekly-hours
limit or overtime threshold is configured.

## Assignment creation boundary

Only a Staff Manager can create or withdraw an assignment. Assignment creation
requires an integer staff ID and existing staff and shift records. Creating a
second active assignment for the same staff member and shift returns a
conflict.

The current assignment write endpoint does not re-run the full candidate
eligibility evaluation. The manager-facing candidate and suggestion flows are
the implemented source of eligibility guidance before assignment, but direct
assignment requests are protected only by the assignment service's record and
duplicate checks. This distinction must not be described as stronger write-time
enforcement than the code provides.

## What happens when someone is unassigned

Unassigning a person changes the existing assignment status to `Cancelled`
instead of deleting the row. This retains the roster record and immediately
removes that assignment from coverage and conflict calculations.

The database permits only one assignment row for each shift and staff member.
Assigning a person again after a `Cancelled` or `Declined` assignment reinstates
that existing row by setting it to `Assigned` rather than creating a duplicate.

Candidate evaluation and AI suggestions never create an assignment. A Staff
Manager must make the final assignment decision explicitly.
