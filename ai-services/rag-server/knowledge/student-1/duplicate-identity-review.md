# Duplicate Identity Review

## Candidate matching in the review page

The duplicate-review page considers other patient profiles as possible matches. It adds 3 points for an exact, case-insensitive first-name match, 3 for an exact last-name match, and 4 for an exact date-of-birth match. Profiles with no matching fields are omitted. Candidates are ordered by score, then by patient ID. This ranking presents candidates for staff review; it does not confirm identity.

## Review decisions

Only profiles with `emergency_override` set and identity review status `Pending` can enter the review workflow. A `Duplicate` decision requires selecting a different candidate and opens the reconciliation page. A `Not duplicate` decision records that status, the candidate ID if supplied, the reviewer name, and a review timestamp.

## Reconciliation requirements

The selected retained Source must be one of the Duplicate profile's potential matches. The form compares patient identity and demographic fields, address, and medical information. Mismatched, empty, or default values require a recorded resolution; the operator can choose a Source value, Duplicate value, or allowed custom value. Required fields and configured select options are checked before submission.

The operator can also select which Duplicate contacts and admissions to retain. The backend rechecks the active profiles, pending emergency review state, and required field-review markers inside a database transaction. If validation fails, the transaction is rolled back.

## Merge result and decision boundary

On a successful merge, selected contacts are copied to the Source profile, selected admissions are reassigned, and the Duplicate's administrative notes are copied. The Duplicate profile is marked `Merged`, deactivated, and recorded as a duplicate of the Source with reviewer and timestamp. The merge operation is an explicit staff workflow; candidate scores do not automatically merge profiles or establish clinical identity.