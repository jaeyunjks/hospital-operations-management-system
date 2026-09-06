"""
external_services.py - outbound calls from Clinical Staff Management to the
other HOMS microservices.

Three services are involved:
  * Patient & Admission - validate patient_id / admission_id, get display details
  * Staff & Shift       - validate doctor_id / nurse_id / specialist_id
                          (all of these are just staff_id values; role is
                           only the name of the column they sit in)
  * Room & Bed          - theatre availability + surgery dispatch

One function per external call:
  get_patient_details(patient_id)
  get_admission_status(admission_id)
  get_staff_details(staff_id)
  get_available_theatre()
  notify_room_and_bed(surgery_request, surgeon_name, bed_id)

ERROR-HANDLING CONTRACT (differs from database_client.py / admission_validation.py
on purpose - these are cross-service calls the routes must branch on, not just
propagate):
  * No function raises. Every function returns a dict with an "ok" key.
  * A timeout / connection failure -> {"ok": False, "error": "unavailable", ...}
    The route can then decide (e.g. 503 to its own caller), never a 500 traceback.
  * get_available_theatre distinguishes THREE outcomes:
        ok + theatre found      -> {"ok": True,  "bed_id": <int>}
        ok + no theatre free    -> {"ok": True,  "bed_id": None, "reason": "none_available"}
        call failed             -> {"ok": False, "error": "unavailable"}
  * notify_room_and_bed distinguishes:
        accepted                -> {"ok": True,  ...}
        409 refusal (success:false in body: clash / maintenance / out of service)
                                -> {"ok": False, "error": "refused", "detail": ...}
        timeout / connection    -> {"ok": False, "error": "unavailable"}

STUB STATUS:
  All five functions are STUBS returning canned responses. Some of the target
  services do not exist yet. Each stub is written so the real HTTP call can be
  dropped in later WITHOUT changing the function signature or return shape -
  see the `# REAL CALL:` comment in each one.

  The stubs for get_available_theatre and notify_room_and_bed read module-level
  simulation switches so the failure / empty / refusal branches can be exercised
  by tests before the real calls exist.
"""

import os

# import requests  # uncomment when the real HTTP calls are wired in

# ------------------------------------------------------------
# Base URLs - env-overridable for Docker, localhost default for local dev.
# Kept here now so switching a stub to a real call is a one-line change.
# ------------------------------------------------------------
PATIENT_ADMISSION_API_URL = os.environ.get(
    "PATIENT_ADMISSION_API_URL", "http://localhost:6100"
).rstrip("/")
STAFF_SHIFT_API_URL = os.environ.get(
    "STAFF_SHIFT_API_URL", "http://localhost:6300"
).rstrip("/")
ROOM_BED_API_URL = os.environ.get(
    "ROOM_BED_API_URL", "http://localhost:6400"
).rstrip("/")

# Per-request timeout (seconds), connect + read. Used by the real calls later.
TIMEOUT = 10


# ============================================================
# STUB SIMULATION SWITCHES
# Flip these (in a test, via monkeypatch, or by env var) to force the
# non-happy paths while everything is still a stub.
#   "ok"      - normal success
#   "empty"   - get_available_theatre: call worked, no theatre free
#   "refused" - notify_room_and_bed: Room & Bed returns 409 success:false
#   "down"    - simulate a timeout / connection failure
# ============================================================
SIMULATE_THEATRE = os.environ.get("SIMULATE_THEATRE", "ok")
SIMULATE_ROOM_DISPATCH = os.environ.get("SIMULATE_ROOM_DISPATCH", "ok")


# ------------------------------------------------------------
# Shared shape helpers - so every function returns the same envelope.
# ------------------------------------------------------------
def _ok(**fields):
    """Success envelope. Extra fields carry the payload."""
    result = {"ok": True}
    result.update(fields)
    return result


def _unavailable(service):
    """Timeout / connection failure envelope - service could not be reached."""
    return {
        "ok": False,
        "error": "unavailable",
        "detail": f"{service} did not respond",
    }


# ============================================================
# Patient & Admission service
# ============================================================
def get_patient_details(patient_id):
    """
    Validate a patient_id and get display details (name, DOB, etc.).

    Returns:
      {"ok": True, "patient": {...}}          - patient exists
      {"ok": False, "error": "not_found"}     - no such patient
      {"ok": False, "error": "unavailable"}   - service down / timed out

    # REAL CALL:
    #   try:
    #       r = requests.get(
    #           f"{PATIENT_ADMISSION_API_URL}/patients/{patient_id}",
    #           timeout=TIMEOUT,
    #       )
    #   except requests.RequestException:
    #       return _unavailable("Patient & Admission")
    #   if r.status_code == 404:
    #       return {"ok": False, "error": "not_found"}
    #   if not r.ok:
    #       return _unavailable("Patient & Admission")
    #   return _ok(patient=r.json())
    """
    # --- STUB: pretend every patient_id 1..9999 exists -------------------
    if not isinstance(patient_id, int) or patient_id <= 0:
        return {"ok": False, "error": "not_found"}
    return _ok(
        patient={
            "patient_id": patient_id,
            "full_name": f"Test Patient {patient_id}",
            "date_of_birth": "1990-01-01",
            "_stub": True,  # remove once the real service is confirmed running
        }
    )


def get_admission_status(admission_id):
    """
    Validate an admission_id and get its current status + display details.

    admission_status is one of: 'Pending', 'Active', 'Cancelled', 'Completed'.
    The caller decides what to do with it (admission_validation.py holds the
    "must be Active to create" rule - this function only reports).

    Returns:
      {"ok": True, "admission": {...}}        - admission exists
      {"ok": False, "error": "not_found"}     - no such admission
      {"ok": False, "error": "unavailable"}   - service down / timed out

    # REAL CALL:
    #   try:
    #       r = requests.get(
    #           f"{PATIENT_ADMISSION_API_URL}/admissions/{admission_id}",
    #           timeout=TIMEOUT,
    #       )
    #   except requests.RequestException:
    #       return _unavailable("Patient & Admission")
    #   if r.status_code == 404:
    #       return {"ok": False, "error": "not_found"}
    #   if not r.ok:
    #       return _unavailable("Patient & Admission")
    #   return _ok(admission=r.json())
    """
    # --- STUB: every positive admission_id is "Active" -------------------
    if not isinstance(admission_id, int) or admission_id <= 0:
        return {"ok": False, "error": "not_found"}
    return _ok(
        admission={
            "admission_id": admission_id,
            "patient_id": admission_id,  # arbitrary, stub only
            "admission_status": "Active",
            "_stub": True,
        }
    )


# ============================================================
# Staff & Shift service
# doctor_id, nurse_id, specialist_id are ALL staff_id values - this one
# function validates and looks up any of them.
# ============================================================
def get_staff_details(staff_id):
    """
    Validate a staff_id and get display details (name, role, specialty).
    Also used to resolve doctor_id -> surgeon_name before a surgery dispatch.

    Returns:
      {"ok": True, "staff": {...}}            - staff member exists
      {"ok": False, "error": "not_found"}     - no such staff member
      {"ok": False, "error": "unavailable"}   - service down / timed out

    # REAL CALL:
    #   try:
    #       r = requests.get(
    #           f"{STAFF_SHIFT_API_URL}/staff/{staff_id}",
    #           timeout=TIMEOUT,
    #       )
    #   except requests.RequestException:
    #       return _unavailable("Staff & Shift")
    #   if r.status_code == 404:
    #       return {"ok": False, "error": "not_found"}
    #   if not r.ok:
    #       return _unavailable("Staff & Shift")
    #   return _ok(staff=r.json())
    """
    # --- STUB: every positive staff_id exists --------------------------
    if not isinstance(staff_id, int) or staff_id <= 0:
        return {"ok": False, "error": "not_found"}
    return _ok(
        staff={
            "staff_id": staff_id,
            "full_name": f"Dr Test Staff {staff_id}",
            "role": "Doctor",
            "specialty": "General Medicine",
            "_stub": True,
        }
    )


# ============================================================
# Room & Bed service
# ============================================================
def get_available_theatre():
    """
    Pick a surgical theatre bed for a new surgery request.

    Room & Bed's POST /api/arrangements will not accept a booking without a
    bed_id, so theatre selection happens here first via
      GET /api/rooms/availability?care_category=Surgical&bed_status=available
    and we choose one of the returned beds.

    THREE distinct outcomes (the calling route branches on these):
      {"ok": True,  "bed_id": <int>}                      - theatre chosen
      {"ok": True,  "bed_id": None, "reason": "none_available"}
                                                          - call worked, nothing free
      {"ok": False, "error": "unavailable"}               - call failed

    # REAL CALL:
    #   try:
    #       r = requests.get(
    #           f"{ROOM_BED_API_URL}/api/rooms/availability",
    #           params={"care_category": "Surgical", "bed_status": "available"},
    #           timeout=TIMEOUT,
    #       )
    #   except requests.RequestException:
    #       return _unavailable("Room & Bed")
    #   if not r.ok:
    #       return _unavailable("Room & Bed")
    #   beds = r.json().get("beds", [])
    #   if not beds:
    #       return _ok(bed_id=None, reason="none_available")
    #   return _ok(bed_id=beds[0]["bed_id"])   # simple pick-first policy
    """
    # --- STUB: behaviour driven by SIMULATE_THEATRE -------------------
    if SIMULATE_THEATRE == "down":
        # Simulated timeout / connection failure.
        return _unavailable("Room & Bed")
    if SIMULATE_THEATRE == "empty":
        # Simulated: availability lookup succeeded but no theatre is free.
        return _ok(bed_id=None, reason="none_available")
    # Simulated success: a theatre bed was available.
    return _ok(bed_id=9001)  # canned stub bed_id


def notify_room_and_bed(surgery_request, surgeon_name, bed_id):
    """
    Dispatch a scheduled surgery to Room & Bed as a room_arrangements row.

    Confirmed contract - fold the surgery_requests row into an arrangement:
      purpose        = "Surgery"
      procedure_name = surgery_request["procedure_type"]
      surgeon_name   = <resolved from doctor_id via get_staff_details>  (a NAME,
                       not an ID - Room & Bed expects a name)
      bed_id         = <resolved earlier by get_available_theatre>
    plus patient_id / admission_id / scheduled_at for context.

    The caller MUST resolve surgeon_name and bed_id before calling this.

    Outcomes the calling route branches on:
      {"ok": True,  "arrangement": {...}}       - Room & Bed accepted it
      {"ok": False, "error": "refused",  "detail": <str>}
            - HTTP 409 with success:false in the body. A real refusal:
              a time clash, a theatre under maintenance, a theatre out of
              service. NOT a transport error - checked explicitly.
      {"ok": False, "error": "unavailable"}     - timeout / connection failure

    # REAL CALL:
    #   payload = {
    #       "purpose": "Surgery",
    #       "procedure_name": surgery_request["procedure_type"],
    #       "surgeon_name": surgeon_name,
    #       "bed_id": bed_id,
    #       "patient_id": surgery_request["patient_id"],
    #       "admission_id": surgery_request["admission_id"],
    #       "scheduled_at": surgery_request["scheduled_at"],
    #   }
    #   try:
    #       r = requests.post(
    #           f"{ROOM_BED_API_URL}/api/arrangements",
    #           json=payload,
    #           timeout=TIMEOUT,
    #       )
    #   except requests.RequestException:
    #       return _unavailable("Room & Bed")
    #   # 409 + success:false is a real refusal, not a transport failure.
    #   if r.status_code == 409:
    #       body = r.json() if r.content else {}
    #       if body.get("success") is False:
    #           return {"ok": False, "error": "refused",
    #                   "detail": body.get("message", "Room & Bed refused the booking")}
    #   if not r.ok:
    #       return _unavailable("Room & Bed")
    #   return _ok(arrangement=r.json())
    """
    # --- STUB: behaviour driven by SIMULATE_ROOM_DISPATCH -------------
    if SIMULATE_ROOM_DISPATCH == "down":
        # Simulated timeout / connection failure.
        return _unavailable("Room & Bed")
    if SIMULATE_ROOM_DISPATCH == "refused":
        # Simulated 409 success:false - clash / maintenance / out of service.
        return {
            "ok": False,
            "error": "refused",
            "detail": "Simulated: theatre unavailable at the requested time",
        }
    # Simulated success: Room & Bed accepted the arrangement.
    return _ok(
        arrangement={
            "purpose": "Surgery",
            "procedure_name": surgery_request.get("procedure_type"),
            "surgeon_name": surgeon_name,
            "bed_id": bed_id,
            "patient_id": surgery_request.get("patient_id"),
            "admission_id": surgery_request.get("admission_id"),
            "scheduled_at": surgery_request.get("scheduled_at"),
            "_stub": True,
        }
    )
