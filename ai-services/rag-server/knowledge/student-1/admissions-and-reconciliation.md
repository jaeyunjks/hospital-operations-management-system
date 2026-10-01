# Admission Scheduling Rules

## Admission scheduling

Admissions created from a patient profile require a date, a start time, and a duration in minutes. Start times use 15-minute blocks. The duration must be at least 15 minutes and use 15-minute blocks; the end time is calculated from the start and duration.

The supported admission statuses are Pending, Active, Cancelled, and Completed. A Pending admission must be scheduled in the future. Active and Completed admissions must have started. Cancelled admissions do not have an additional time restriction in the profile workflow.
