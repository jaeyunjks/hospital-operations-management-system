# Shift Planning and Coverage

## Shift fields and validation

A shift records:

- department;
- shift date in `YYYY-MM-DD` format;
- start and end times in 24-hour `HH:MM` format;
- required role;
- required staff count;
- shift status; and
- optional notes.

The required staff count must be a positive integer and defaults to 1 when it
is omitted. The required role must match a role held by at least one current
staff record. Department is not restricted to departments already represented
in the workforce.

Shift operations are restricted to the Staff Manager role. The accepted shift
statuses are `Planned`, `Open`, `Filled`, `Completed`, and `Cancelled`. The
current implementation validates the value but does not impose a status
transition sequence or derive the status from coverage.

## Custom and overnight shift times

Shift times are manager-controlled. The backend accepts any valid start and end
times rather than limiting shifts to named dayparts or a fixed duration. Equal
start and end times are rejected when a shift is created.

An end time earlier than the start time represents an overnight shift ending on
the following calendar day. Overlap and weekly-availability checks use this
overnight interpretation. Adjacent shifts, where one ends exactly when another
starts, do not overlap.

## Per-shift coverage calculation

Coverage counts assignment records whose status is not `Cancelled` or
`Declined`. For each shift:

- `assigned_staff_count` is the number of counted assignments;
- `filled_staff_count` is the smaller of assigned staff and required staff;
- `shortfall` is `max(required - assigned, 0)`;
- `surplus` is `max(assigned - required, 0)`; and
- `coverage_status` is `Unstaffed`, `Understaffed`, `Fully staffed`, or
  `Overstaffed`.

`Unstaffed` means nobody is assigned when at least one person is required.
`Understaffed` means some positions remain unfilled. `Fully staffed` means the
assigned and required counts are equal. `Overstaffed` means assigned staff
exceed the requirement.

## Overall coverage calculation

Overall coverage sums figures that were calculated per shift. A surplus on one
shift cannot cancel a shortfall on another shift. `understaffed` includes every
shift with a gap, including shifts also counted as `unstaffed`.

`coverage_pct` is the rounded percentage of filled positions divided by
required positions. Filled positions are capped per shift at the requirement,
so the percentage cannot exceed 100%. Surplus positions are reported
separately. When there are no required positions, `coverage_pct` is `null`
because there is no coverage to report.

## What coverage does and does not mean

Coverage describes persisted staffing allocations against manager-entered
requirements. It does not prove that an assigned person remains operationally
available, matches their recurring weekly availability, or has no new approved
unavailability request. An existing active assignment continues to count until
it is explicitly cancelled or declined.

Coverage does not determine clinical staffing policy, medical need, ward
occupancy, or future bed demand. Room & Bed information does not change a
shift's required staff count. Coverage calculations also do not assign,
unassign, replace, or recommend employees and do not automatically change a
shift's status.
