# Patient Intake and Record Data

## Standard intake requirements

The standard intake form requires a title, first name, last name, date of birth, sex, mobile number, street address, suburb, state, postcode, and Medicare number. The form checks that these values are non-blank before creating a patient. The application does not perform a format check on the Medicare number in this form.

After the patient API returns an ID, intake creates a primary address and medical-information record. An optional non-empty intake note is saved as a patient administrative note. If the patient creation request fails, the frontend displays an error; it does not persist an offline submission queue.

## Emergency intake defaults

Selecting Emergency Override bypasses the standard non-blank field checks. The patient record is created with `emergency_override` set and identity review status `Pending`. Missing fields receive application placeholders such as `Unknown`, `Patient`, `1900-01-01`, `Unassigned`, `0000000000`, `UNKNOWN`, and default address or insurance values. These are data-entry placeholders, not verified identity or insurance details.

The database stores the patient first, then the frontend creates related address and medical-information rows using the returned patient ID. The identity review workflow is separate from this intake submission.

## Patient and related records

The Student-1 database owns patient profiles, addresses, insurance and Medicare information, emergency contacts, patient administrative notes, and admissions. Patient status values allowed by the database are `Active`, `Inactive`, `Deceased`, `Transferred`, and `Merged`; new patients default to `Active`.

The database constrains assigned sex, contact method, marital status, First Nations heritage, boolean flags, and Australian state or territory values. Addresses, medical information, contacts, notes, and admissions reference a patient ID. Admissions store a status of `Pending`, `Active`, `Cancelled`, or `Completed`.

## Patient search visibility

Patient search defaults to `Active` records. A caller whose displayed role is not `System Admin` cannot use the search page to select another status; the frontend resets the filter to `Active`. The emergency filter selects records with `emergency_override` set.