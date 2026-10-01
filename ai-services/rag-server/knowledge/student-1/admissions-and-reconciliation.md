# Patient and Admission Workflow Rules

## Admission scheduling

Admissions created from a patient profile require a date, a start time, and a duration in minutes. Start times use 15-minute blocks. The duration must be at least 15 minutes and use 15-minute blocks; the end time is calculated from the start and duration.

The supported admission statuses are Pending, Active, Cancelled, and Completed. A Pending admission must be scheduled in the future. Active and Completed admissions must have started. Cancelled admissions do not have an additional time restriction in the profile workflow.

## Provisional emergency identity

An emergency identity is marked as requiring reconciliation when its identity-strength score is below 6. The service adds 2 points for each supplied first name, last name, date of birth, Medicare number, and patient ID. Missing names are represented as Unknown in the provisional identity record.

## Duplicate profile reconciliation

The reconciliation page accepts a duplicate only when it is awaiting emergency identity review and the retained source profile is one of the potential matches. The operator resolves profile fields and can choose which duplicate contacts and admissions to retain. The reconciliation request records who reviewed it and is submitted to the Patient & Admissions backend. This workflow documents application behavior; it does not determine clinical identity or provide clinical advice.