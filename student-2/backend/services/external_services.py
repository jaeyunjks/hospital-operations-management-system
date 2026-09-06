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

CONTRACT-MISMATCH HANDLING (added when the real calls were wired in):
  The contracts below were built against local stubs and earlier team
  discussion, never against the other members' services actually running. When
  a real response does NOT match the assumed shape - a field renamed, a status
  code different, an envelope wrapped one level deeper - these functions do NOT
  silently coerce or guess. They return

        {"ok": False, "error": "contract_mismatch", "detail": "<precise reason>"}

  naming exactly which field / status code / URL disagreed, so the mismatch is
  fixed at the source (either their service or this module's assumption) rather
  than papered over here. "contract_mismatch" is a distinct error kind from
  "unavailable" / "not_found" / "refused"; callers that don't recognise it fall
  through to their own defensive branch and surface the detail.

STUB STATUS:
  Every function can still run as a STUB returning canned responses, so this
  feature is developable when a teammate's service is down or not built yet.
  The real HTTP call is the default; the stub path is opt-in:

    * USE_EXTERNAL_STUBS=true                 - ALL five functions use stubs
    * SIMULATE_THEATRE       in {ok,empty,down}   - forces get_available_theatre
                                                   onto the stub with that outcome
    * SIMULATE_ROOM_DISPATCH in {ok,refused,down} - forces notify_room_and_bed
                                                   onto the stub with that outcome

  The original stub bodies are preserved verbatim below as _stub_* functions;
  nothing was deleted.
"""

import os

import requests

# ------------------------------------------------------------
# Base URLs - env-overridable for Docker, localhost default for local dev.
#
# In the shared docker-compose.yml these are set to the other members'
# docker-compose SERVICE NAMES (never localhost), e.g.
#   PATIENT_ADMISSION_API_URL=http://student-1-backend:5100/api
#   STAFF_SHIFT_API_URL=http://student5-backend:5500/api
#   ROOM_BED_API_URL=http://student-4-backend:5400/api
#
# The localhost defaults below are for running THIS backend outside compose
# with the three dependencies port-forwarded on their real API ports. They are
# only ever reached when USE_EXTERNAL_STUBS is false AND compose did not set the
# variable - in practice the stub path is what a lone local dev uses.
# ------------------------------------------------------------
PATIENT_ADMISSION_API_URL = os.environ.get(
    "PATIENT_ADMISSION_API_URL", "http://localhost:5100/api"
).rstrip("/")
STAFF_SHIFT_API_URL = os.environ.get(
    "STAFF_SHIFT_API_URL", "http://localhost:5500/api"
).rstrip("/")
ROOM_BED_API_URL = os.environ.get(
    "ROOM_BED_API_URL", "http://localhost:5400/api"
).rstrip("/")

# Per-request timeout (seconds), connect + read.
TIMEOUT = 10


# ============================================================
# STUB CONTROLS
#   USE_EXTERNAL_STUBS - master switch: "true" routes every function to its
#                        _stub_* body. Use when developing offline or when a
#                        teammate's service is down.
#   SIMULATE_THEATRE / SIMULATE_ROOM_DISPATCH - per-call override that ALSO
#                        forces the stub, and picks which branch it exercises:
#     "ok"      - normal success
#     "empty"   - get_available_theatre: call worked, no theatre free
#     "refused" - notify_room_and_bed: Room & Bed returns 409 success:false
#     "down"    - simulate a timeout / connection failure
#   Leaving SIMULATE_* unset (the default) means the real call is used unless
#   USE_EXTERNAL_STUBS is true.
# ============================================================
def _use_stubs():
    return os.environ.get("USE_EXTERNAL_STUBS", "false").strip().lower() == "true"


SIMULATE_THEATRE = os.environ.get("SIMULATE_THEATRE", "").strip().lower()
SIMULATE_ROOM_DISPATCH = os.environ.get("SIMULATE_ROOM_DISPATCH", "").strip().lower()


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


def _mismatch(detail):
    """
    Contract-mismatch envelope - the real response did not match what this
    module was built to expect. `detail` names exactly what disagreed.
    """
    return {"ok": False, "error": "contract_mismatch", "detail": detail}


def _json_or_none(response):
    """Parse a response body as JSON, or None if it isn't JSON."""
    try:
        return response.json()
    except ValueError:
        return None


# ============================================================
# Patient & Admission service  (student-1-backend, real routes under /api)
#   GET {PATIENT_ADMISSION_API_URL}/patients/<id>
#   GET {PATIENT_ADMISSION_API_URL}/admissions/<id>
# Both return the BARE table row (no envelope, no wrapper key) on hit.
# ============================================================
def get_patient_details(patient_id):
    """
    Validate a patient_id and get display details.

    Returns:
      {"ok": True, "patient": {...}}                - patient exists
      {"ok": False, "error": "not_found"}           - no such patient
      {"ok": False, "error": "unavailable"}         - service down / timed out
      {"ok": False, "error": "contract_mismatch", "detail": ...}
                                                    - response shape unexpected

    NOTE: not currently called by any route in this service (admission_validation
    .py makes its own admission-status call). Kept to contract so a future caller
    can rely on the signature and return shape.
    """
    if _use_stubs():
        return _stub_get_patient_details(patient_id)

    url = f"{PATIENT_ADMISSION_API_URL}/patients/{patient_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException:
        return _unavailable("Patient & Admission")

    body = _json_or_none(r)

    # Miss. Patient & Admission's DataError handler returns HTTP 400 (not 404)
    # with {"error": "...not found"} for an unknown id, so treat either a 404 or
    # a 400-whose-body-says-not-found as "not_found". Anything else 4xx/5xx is a
    # real failure.
    if r.status_code == 404:
        return {"ok": False, "error": "not_found"}
    if r.status_code == 400 and isinstance(body, dict) and "not found" in str(
        body.get("error", "")
    ).lower():
        return {"ok": False, "error": "not_found"}
    if not r.ok:
        return _unavailable("Patient & Admission")

    if not isinstance(body, dict):
        return _mismatch(
            f"GET {url} returned {r.status_code} with a non-object body "
            f"({type(body).__name__}); expected the patient row as a JSON object"
        )
    # Patient & Admission serves the bare row on this route. If it is instead
    # wrapped ({"data": {...}} or {"patient": {...}}), say so rather than guess.
    if "patient_id" not in body:
        if "data" in body or "patient" in body:
            return _mismatch(
                f"GET {url} wrapped the patient in "
                f"'{'data' if 'data' in body else 'patient'}'; expected the bare "
                f"row (a top-level 'patient_id')"
            )
        return _mismatch(
            f"GET {url} response has no 'patient_id' field; got keys "
            f"{sorted(body)}"
        )
    return _ok(patient=body)


def get_admission_status(admission_id):
    """
    Validate an admission_id and get its current status + display details.

    admission_status is one of: 'Pending', 'Active', 'Cancelled', 'Completed'.

    Returns:
      {"ok": True, "admission": {...}}              - admission exists
      {"ok": False, "error": "not_found"}           - no such admission
      {"ok": False, "error": "unavailable"}         - service down / timed out
      {"ok": False, "error": "contract_mismatch", "detail": ...}
                                                    - response shape unexpected
    """
    if _use_stubs():
        return _stub_get_admission_status(admission_id)

    url = f"{PATIENT_ADMISSION_API_URL}/admissions/{admission_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException:
        return _unavailable("Patient & Admission")

    body = _json_or_none(r)

    if r.status_code == 404:
        return {"ok": False, "error": "not_found"}
    if r.status_code == 400 and isinstance(body, dict) and "not found" in str(
        body.get("error", "")
    ).lower():
        return {"ok": False, "error": "not_found"}
    if not r.ok:
        return _unavailable("Patient & Admission")

    if not isinstance(body, dict):
        return _mismatch(
            f"GET {url} returned {r.status_code} with a non-object body "
            f"({type(body).__name__}); expected the admission row as a JSON object"
        )
    if "admission_status" not in body:
        if isinstance(body.get("data"), dict) and "admission_status" in body["data"]:
            return _mismatch(
                f"GET {url} wrapped the admission in 'data'; expected the bare "
                f"row with a top-level 'admission_status'"
            )
        if isinstance(body.get("admission"), dict):
            return _mismatch(
                f"GET {url} wrapped the admission in 'admission'; expected the "
                f"bare row with a top-level 'admission_status'"
            )
        return _mismatch(
            f"GET {url} response has no 'admission_status' field; got keys "
            f"{sorted(body)}"
        )

    valid = {"Pending", "Active", "Cancelled", "Completed"}
    if body["admission_status"] not in valid:
        return _mismatch(
            f"GET {url} returned admission_status="
            f"{body['admission_status']!r}; expected one of {sorted(valid)}"
        )
    return _ok(admission=body)


# ============================================================
# Staff & Shift service  (student5-backend, real routes under /api)
#   GET {STAFF_SHIFT_API_URL}/staff/<id>
# Success body: {"staff": {"staff_id", "name", "role", "department",
#                          "specialisation", ...}}  (record wrapped in "staff")
# Miss: HTTP 404 {"error": "not_found", "message": ...}
# doctor_id, nurse_id, specialist_id are ALL staff_id values - this one
# function validates and looks up any of them.
# ============================================================
def get_staff_details(staff_id):
    """
    Validate a staff_id and get display details.
    Also used to resolve doctor_id -> surgeon name before a surgery dispatch;
    the caller reads result["staff"]["full_name"].

    Returns:
      {"ok": True, "staff": {...}}                  - staff member exists.
            The dict ALWAYS carries a "full_name" key (mapped from the source
            "name" field) so callers relying on the agreed contract keep working.
      {"ok": False, "error": "not_found"}           - no such staff member
      {"ok": False, "error": "unavailable"}         - service down / timed out
      {"ok": False, "error": "contract_mismatch", "detail": ...}
                                                    - response shape unexpected
    """
    if _use_stubs():
        return _stub_get_staff_details(staff_id)

    url = f"{STAFF_SHIFT_API_URL}/staff/{staff_id}"
    try:
        r = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException:
        return _unavailable("Staff & Shift")

    if r.status_code == 404:
        return {"ok": False, "error": "not_found"}
    if r.status_code == 403:
        # Staff & Shift gates /staff/<id> behind require_self_or_manager; a
        # header-less caller is treated as Staff Manager, so a 403 here means
        # that default changed. Surface it - do not treat it as "not_found".
        return _mismatch(
            f"GET {url} returned 403; this call sends no identity header and "
            f"relied on Staff & Shift defaulting a header-less caller to Staff "
            f"Manager. That default appears to have changed."
        )
    if not r.ok:
        return _unavailable("Staff & Shift")

    body = _json_or_none(r)
    if not isinstance(body, dict):
        return _mismatch(
            f"GET {url} returned {r.status_code} with a non-object body "
            f"({type(body).__name__}); expected {{'staff': {{...}}}}"
        )

    # Real service wraps the record in "staff". Accept either the wrapped form
    # or a bare row, but name the shape if it is neither.
    if isinstance(body.get("staff"), dict):
        record = body["staff"]
    elif "staff_id" in body:
        record = body
    else:
        return _mismatch(
            f"GET {url} response is neither {{'staff': {{...}}}} nor a bare row "
            f"with 'staff_id'; got keys {sorted(body)}"
        )

    # The caller needs result["staff"]["full_name"]. Staff & Shift's field is
    # "name". Map it; if NEITHER is present, that is a contract mismatch to
    # report, not a nameless surgeon to dispatch.
    name = record.get("full_name") or record.get("name")
    if not name:
        return _mismatch(
            f"GET {url} staff record for id {staff_id} has neither 'full_name' "
            f"nor 'name'; got keys {sorted(record)}. A surgeon name cannot be "
            f"resolved for Room & Bed."
        )

    normalised = dict(record)
    normalised["full_name"] = name
    # Also expose the agreed 'specialty' alias if only 'specialisation' is set,
    # so any caller using the old field name still works.
    if "specialty" not in normalised and "specialisation" in normalised:
        normalised["specialty"] = normalised["specialisation"]
    return _ok(staff=normalised)


# ============================================================
# Room & Bed service  (student-4-backend, real routes under /api)
#   GET  {ROOM_BED_API_URL}/theatres/board
#        -> {"success": true, "data": {"theatres": [ ... ]}, "error": null}
#           each theatre: {"bed_id", "room_status", "bed_status", ...}
#   POST {ROOM_BED_API_URL}/arrangements
#        accepted -> HTTP 201 {"success": true, "data": <arrangement>, "error": null}
#        refused  -> HTTP 409 {"success": false, "data": null, "error": "<reason>"}
# ============================================================
_AVAILABLE_BED_STATUS = "available"
_AVAILABLE_ROOM_STATUS = "Available"


def get_available_theatre():
    """
    Pick a surgical theatre bed for a new surgery request.

    THREE distinct outcomes (the calling route branches on these):
      {"ok": True,  "bed_id": <int>}                      - theatre chosen
      {"ok": True,  "bed_id": None, "reason": "none_available"}
                                                          - call worked, nothing free
      {"ok": False, "error": "unavailable"}               - call failed
      {"ok": False, "error": "contract_mismatch", "detail": ...}
                                                          - response shape unexpected
    """
    if SIMULATE_THEATRE in {"ok", "empty", "down"}:
        return _stub_get_available_theatre(SIMULATE_THEATRE)
    if _use_stubs():
        return _stub_get_available_theatre("ok")

    url = f"{ROOM_BED_API_URL}/theatres/board"
    try:
        r = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException:
        return _unavailable("Room & Bed")
    if not r.ok:
        return _unavailable("Room & Bed")

    body = _json_or_none(r)
    if not isinstance(body, dict):
        return _mismatch(
            f"GET {url} returned a non-object body ({type(body).__name__}); "
            f"expected the {{'success', 'data', 'error'}} envelope"
        )
    if body.get("success") is not True:
        return _mismatch(
            f"GET {url} returned no success:true envelope; got keys {sorted(body)}"
        )
    if not isinstance(body.get("data"), dict):
        return _mismatch(
            f"GET {url} response has no object 'data' payload; got keys {sorted(body)}"
        )
    theatres = body["data"].get("theatres")
    if not isinstance(theatres, list):
        return _mismatch(
            f"GET {url} data.theatres is {type(theatres).__name__}, not a list"
        )
    if not theatres:
        return _ok(bed_id=None, reason="none_available")

    # The board is the authoritative theatre view. Only an available room with
    # an available theatre bed is eligible; generic Surgical beds are excluded.
    for row in theatres:
        if not isinstance(row, dict) or "bed_id" not in row:
            row_desc = sorted(row) if isinstance(row, dict) else repr(row)
            return _mismatch(
                f"GET {url} returned a theatre row without a 'bed_id' field; row "
                f"was {row_desc}"
            )
        if (
            row.get("room_status") == _AVAILABLE_ROOM_STATUS
            and row.get("bed_status") == _AVAILABLE_BED_STATUS
        ):
            return _ok(bed_id=row["bed_id"])

    return _ok(bed_id=None, reason="none_available")


def notify_room_and_bed(surgery_request, surgeon_name, bed_id):
    """
    Dispatch a scheduled surgery to Room & Bed as a room arrangement.

    The caller MUST resolve surgeon_name and bed_id before calling this.

    Outcomes the calling route branches on:
      {"ok": True,  "arrangement": {...}}       - Room & Bed accepted it
      {"ok": False, "error": "refused",  "detail": <str>}
            - HTTP 409 + success:false. A real refusal: a time clash, a theatre
              under maintenance, a theatre out of service. NOT a transport error.
      {"ok": False, "error": "unavailable"}     - timeout / connection failure
      {"ok": False, "error": "contract_mismatch", "detail": ...}
            - response shape / status unexpected

    Room & Bed's POST /arrangements requires 'start_time' (not 'scheduled_at'),
    plus 'procedure_name' and 'surgeon_name' for a Surgery. The surgery_request
    row from this service carries 'scheduled_at' and 'procedure_type', so both
    are re-mapped here to the names Room & Bed expects.
    """
    if SIMULATE_ROOM_DISPATCH in {"ok", "refused", "down"}:
        return _stub_notify_room_and_bed(
            SIMULATE_ROOM_DISPATCH, surgery_request, surgeon_name, bed_id
        )
    if _use_stubs():
        return _stub_notify_room_and_bed(
            "ok", surgery_request, surgeon_name, bed_id
        )

    # scheduled_at -> start_time, procedure_type -> procedure_name.
    start_time = surgery_request.get("scheduled_at")
    procedure_name = surgery_request.get("procedure_type")
    if not start_time:
        return _mismatch(
            "surgery_request has no 'scheduled_at' to map to Room & Bed's "
            "required 'start_time'"
        )
    if not procedure_name:
        return _mismatch(
            "surgery_request has no 'procedure_type' to map to Room & Bed's "
            "required 'procedure_name'"
        )

    payload = {
        "purpose": "Surgery",
        "procedure_name": procedure_name,
        "surgeon_name": surgeon_name,
        "bed_id": bed_id,
        "patient_id": surgery_request.get("patient_id"),
        "admission_id": surgery_request.get("admission_id"),
        "start_time": start_time,
    }

    url = f"{ROOM_BED_API_URL}/arrangements"
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
    except requests.RequestException:
        return _unavailable("Room & Bed")

    body = _json_or_none(r)

    # 409 + success:false is a real refusal, checked explicitly and kept
    # distinct from a transport failure.
    if r.status_code == 409:
        if isinstance(body, dict) and body.get("success") is False:
            detail = body.get("error") or body.get("message") or body.get("detail")
            if not detail:
                return _mismatch(
                    f"POST {url} returned 409 success:false but carried no "
                    f"'error' message to surface; body keys {sorted(body)}"
                )
            return {"ok": False, "error": "refused", "detail": detail}
        got = sorted(body) if isinstance(body, dict) else repr(body)
        return _mismatch(
            f"POST {url} returned 409 but the body is not "
            f"{{'success': false, 'error': '<reason>'}}; got {got}"
        )

    if not r.ok:
        # Any other non-2xx: we can't tell if the arrangement was recorded.
        # Treat as unavailable (route deletes its local row and reports an
        # uncertain outcome), but attach the status so it's diagnosable.
        return {
            "ok": False,
            "error": "unavailable",
            "detail": f"Room & Bed returned HTTP {r.status_code} for POST {url}",
        }

    if not isinstance(body, dict):
        return _mismatch(
            f"POST {url} returned {r.status_code} with a non-object body "
            f"({type(body).__name__}); expected the {{'success','data','error'}} "
            f"envelope"
        )
    if body.get("success") is not True:
        return _mismatch(
            f"POST {url} returned {r.status_code} without success:true; body "
            f"keys {sorted(body)}"
        )
    if "data" not in body:
        return _mismatch(
            f"POST {url} success response has no 'data' (the created "
            f"arrangement); got keys {sorted(body)}"
        )
    return _ok(arrangement=body["data"])


# ============================================================
# ============================================================
# ORIGINAL STUBS - preserved verbatim, reachable via USE_EXTERNAL_STUBS=true
# or the SIMULATE_* switches. Not called unless a stub path is selected above.
# Each returns exactly the same envelope shape as its real counterpart.
# ============================================================
# ============================================================
def _stub_get_patient_details(patient_id):
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


def _stub_get_admission_status(admission_id):
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


def _stub_get_staff_details(staff_id):
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


def _stub_get_available_theatre(mode):
    # --- STUB: behaviour driven by `mode` (ok | empty | down) ----------
    if mode == "down":
        # Simulated timeout / connection failure.
        return _unavailable("Room & Bed")
    if mode == "empty":
        # Simulated: availability lookup succeeded but no theatre is free.
        return _ok(bed_id=None, reason="none_available")
    # Simulated success: a theatre bed was available.
    return _ok(bed_id=9001)  # canned stub bed_id


def _stub_notify_room_and_bed(mode, surgery_request, surgeon_name, bed_id):
    # --- STUB: behaviour driven by `mode` (ok | refused | down) --------
    if mode == "down":
        # Simulated timeout / connection failure.
        return _unavailable("Room & Bed")
    if mode == "refused":
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
