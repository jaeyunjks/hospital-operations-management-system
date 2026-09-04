"""
admission_validation.py - check admission status against the Patient &
Admission Management service.

Several route files need to know whether an admission is currently active
before they act. That fact lives in another microservice, not in our
database, so we ask it over HTTP.

Assumed contract:
  GET <PATIENT_ADMISSION_API_URL>/admissions/<admission_id>
  -> 200 {"admission_id": 123, "patient_id": 45, "admission_status": "Active"}

admission_status is one of: 'Pending', 'Active', 'Cancelled', 'Completed'.
Only 'Active' counts as active here.

Two callers, two behaviours:
  * clinical_records / surgery_requests - must NOT create a record unless the
    admission is active. Use require_active_for_create(); it raises.
  * consultation_requests - an open request should be auto-cancelled if the
    admission has since gone inactive. Use cancel_if_inactive(); it returns
    a bool the caller acts on.

Both go through one shared helper, _fetch_admission_status(), so the HTTP
call is written once. If the Patient & Admission API is unreachable, that
helper raises AdmissionServiceError - callers are expected to catch it.

STUB STATUS
-----------
The Patient & Admission Management service is not part of this branch's stack
(see docker-compose.yml), so _fetch_admission_status() is a STUB - the same
approach services/external_services.py already takes for the other absent
services. It returns a canned status instead of making the HTTP call, so the
create-gate and auto-cancel rules can be exercised end-to-end. The real call
is kept verbatim in the `# REAL CALL:` block below; dropping it back in is a
one-line change and needs no change to either public function.

  SIMULATE_ADMISSION env var forces the non-happy paths while this is a stub:
    "Active" (default) | "Pending" | "Cancelled" | "Completed" - canned status
    "down"                                        - simulate the service down
"""

import os

import requests  # noqa: F401  (kept for the REAL CALL block below)

# Stub simulation switch - flip via env var / monkeypatch to exercise the
# not-active and service-down branches while the real service is absent.
SIMULATE_ADMISSION = os.environ.get("SIMULATE_ADMISSION", "Active")

# Base URL of the Patient & Admission Management service. Env-overridable for
# Docker/deployment; localhost default for local dev.
BASE_URL = os.environ.get(
    "PATIENT_ADMISSION_API_URL", "http://localhost:6100"
).rstrip("/")

# Per-request timeout in seconds (connect + read).
TIMEOUT = 10

# The only admission_status value that means "active".
ACTIVE_STATUS = "Active"


class AdmissionServiceError(Exception):
    """
    Raised when we cannot get a usable admission status:
      * the Patient & Admission API is unreachable / timed out
      * it returned a non-2xx status
      * it returned a body we can't understand (not JSON, missing field)

    `status_code` is the HTTP status if there was a response, else None.
    """

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class AdmissionNotActiveError(Exception):
    """
    Raised by require_active_for_create() when the admission exists but is
    not active. `admission_status` holds the actual status reported.
    """

    def __init__(self, admission_id, admission_status):
        super().__init__(
            f"admission {admission_id} is '{admission_status}', not active"
        )
        self.admission_id = admission_id
        self.admission_status = admission_status


# ------------------------------------------------------------
# Shared internal helper - the single place the HTTP call happens.
# ------------------------------------------------------------
def _fetch_admission_status(admission_id):
    """
    Return one admission's admission_status string.

    STUB: the Patient & Admission service is not in this branch's stack, so
    this returns SIMULATE_ADMISSION instead of calling it. Raises
    AdmissionServiceError when SIMULATE_ADMISSION == "down", exactly as the
    real transport failure would.

    # REAL CALL:
    #   url = f"{BASE_URL}/admissions/{admission_id}"
    #   try:
    #       response = requests.get(url, timeout=TIMEOUT)
    #   except requests.RequestException as exc:
    #       raise AdmissionServiceError(
    #           f"could not reach Patient & Admission API at {url}: {exc}"
    #       ) from exc
    #   if not response.ok:
    #       raise AdmissionServiceError(
    #           f"Patient & Admission API returned {response.status_code} "
    #           f"for admission {admission_id}",
    #           status_code=response.status_code,
    #       )
    #   try:
    #       body = response.json()
    #   except ValueError as exc:
    #       raise AdmissionServiceError(
    #           f"Patient & Admission API returned non-JSON for admission "
    #           f"{admission_id}: {response.text}"
    #       ) from exc
    #   status = body.get("admission_status")
    #   if not status:
    #       raise AdmissionServiceError(
    #           f"Patient & Admission API response for admission {admission_id} "
    #           f"has no admission_status: {body}"
    #       )
    #   return status
    """
    if SIMULATE_ADMISSION == "down":
        raise AdmissionServiceError(
            f"Patient & Admission service unavailable (stub) for admission "
            f"{admission_id}"
        )
    return SIMULATE_ADMISSION


# ------------------------------------------------------------
# Public behaviour 1: prevent an action.
# ------------------------------------------------------------
def require_active_for_create(admission_id):
    """
    Guard for clinical_records / surgery_requests creation.

    Does nothing if the admission is active. Raises AdmissionNotActiveError
    if it is not, or AdmissionServiceError if the status can't be fetched.
    """
    status = _fetch_admission_status(admission_id)
    if status != ACTIVE_STATUS:
        raise AdmissionNotActiveError(admission_id, status)


# ------------------------------------------------------------
# Public behaviour 2: react to a status change.
# ------------------------------------------------------------
def cancel_if_inactive(admission_id):
    """
    Check for consultation_requests: has this admission gone inactive?

    Returns True if the caller should auto-cancel the open request (admission
    is anything other than 'Active'), False if it should be left alone.

    Raises AdmissionServiceError if the status can't be fetched - an
    unreachable service is not treated as "inactive".
    """
    status = _fetch_admission_status(admission_id)
    return status != ACTIVE_STATUS
