# Emergency Admission and Capacity Workflow

## Provisional identity strength

The emergency helper adds 2 identity-strength points for each supplied first name, last name, date of birth, Medicare number, and patient ID. A score below 6 sets `requires_reconciliation` to true. Missing first or last names are represented as `Unknown` in the provisional identity response.

## Emergency candidate matching

The emergency helper can rank a supplied list of candidate records. It adds 3 points for an exact first-name match, 3 for an exact last-name match, 4 for an exact date-of-birth match, and 5 for an exact Medicare-number match. Results are ordered by descending score. The `/api/admissions/emergency` route accepts candidates in `possible_matches` or `candidate_patients`; the candidate list must be supplied by the caller.

## Admission and capacity response

The emergency route requires an `identity` object. It returns possible matches, a provisional-patient result, an emergency admission result, and a capacity-allocation result. The admission response defaults priority to `Emergency`, uses status `active`, and marks data quality `provisional` when identity reconciliation is required or `confirmed` otherwise.

The capacity helper returns the supplied capacity ID, assignee, reason, assignment time, and status `allocated`. The route defaults the capacity ID to `unassigned`, the assignee to `emergency team`, and the reason to `Emergency priority`. This helper formats a response; it does not check live bed availability.

## Persistence and feature boundaries

The current `/api/admissions/emergency` route returns the helper results without calling the database service, so this response does not itself persist a patient, admission, or capacity assignment. Clinical documentation, clinician assignment, and care-task planning are outside Student-1 and belong to the Clinical Staff Management feature.