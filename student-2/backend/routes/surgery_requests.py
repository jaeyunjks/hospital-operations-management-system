"""
surgery_requests.py - Flask Blueprint for the surgery_requests table.

Mounted at /api/surgery-requests by app.py.

    POST   /                                 doctor schedules a surgery for an
                                             admitted patient (dispatches to
                                             Room & Bed on success)
    GET    /admission/<admission_id>          every surgery request raised for
                                             one admission

WHY THIS ROUTE IS DIFFERENT FROM THE OTHER TABLES
-------------------------------------------------
Creating a surgery_requests row is not just a local write - it is the thing that
dispatches a theatre-prep request to the Room & Bed Management service. So the
POST does more work than a plain create, and the ORDER of that work matters:

    1. Auth        - only the admission's assigned doctor may schedule.
    2. Admission   - blocked (409) if the admission is not active
                     (schema.sql header rule, same as clinical_records).
    3. Theatre     - get_available_theatre() must return a real bed_id.
                     No theatre free  -> 409, NOTHING is written.
                     Lookup failed    -> 503, NOTHING is written.
    4. Surgeon     - resolve doctor_id -> surgeon NAME via get_staff_details()
                     (Room & Bed wants a name, not an id).
    5. Local row   - create_surgery_request() with the RESOLVED bed_id. This is
                     the first and only local write, and it only happens once a
                     bed_id is confirmed.
    6. Dispatch    - notify_room_and_bed(). If it fails, the row created in
                     step 5 is DELETED before we respond (see below).

FAILURE HANDLING AFTER THE ADMISSION CHECK PASSES
-------------------------------------------------
Point 1 - get_available_theatre() finds no theatre
    {"ok": True, "bed_id": None, "reason": "none_available"}
    The call succeeded; there is simply no capacity. The schema declares
    bed_id NOT NULL and the row's existence means "a real, dispatchable
    booking", so a row must NEVER be created here. The doctor gets a clean
    409 (well-formed, authorised, but the current state can't satisfy it)
    with an explicit "nothing was saved" message. This is deliberately not a
    503 - the system worked, the answer is "no capacity, try later".

Point 2 - bed found, local row created, but notify_room_and_bed() fails
    The dispatch contract (external_services.py) gives TWO distinct failures:

      {"ok": False, "error": "refused", "detail": <str>}   - HTTP 409 +
          success:false. A definitive "no" from Room & Bed: time clash,
          theatre under maintenance, theatre out of service - something the
          availability lookup didn't catch (a race, or a condition it doesn't
          model). This will not change on a plain retry.
          -> DELETE the local row (Room & Bed has authoritatively rejected
             the booking; keeping a 'scheduled' row would assert a surgery
             the theatre service refused, and surgery_requests.status has no
             'failed'/'refused' state anyway). Respond 409 with the refusal
             detail surfaced verbatim.

      {"ok": False, "error": "unavailable"}                - timeout /
          connection failure. We do NOT know whether Room & Bed processed the
          arrangement. There is no reconciliation job, and Room & Bed owns the
          real prep state, so an unverifiable local 'scheduled' row is worse
          than no row.
          -> DELETE the local row and respond 502 with an explicitly
             UNCERTAIN message (outcome unknown, nothing saved locally, safe
             to retry but watch for a duplicate theatre booking).

    Net rule: the surgery_requests row is only ever left in place when Room &
    Bed has confirmed acceptance. Any dispatch failure -> delete the row.

CONSTRAINT COMPLIANCE
    * notify_room_and_bed() is never called before a bed_id has been resolved
      via get_available_theatre() - see the ordering above.
    * "refused" (409) and "unavailable" (timeout/connection) are branched on
      separately, using the distinct return values from external_services.py,
      and both are surfaced clearly in the response body.

All local writes go through services.database_client - no raw SQL here.
"""

from flask import Blueprint, jsonify, request

from auth import AuthError, get_current_user, require_role
from services import database_client as db
from services.admission_validation import (
    AdmissionNotActiveError,
    AdmissionServiceError,
    require_active_for_create,
)
from services.external_services import (
    get_available_theatre,
    get_staff_details,
    notify_room_and_bed,
)

surgery_requests_bp = Blueprint("surgery_requests", __name__)

# ------------------------------------------------------------
# Status constants (schema.sql, table 4: 'scheduled' | 'completed' | 'cancelled').
# A new request always starts 'scheduled'. There is deliberately no 'failed'
# state - a request that could not be dispatched is not persisted at all.
# ------------------------------------------------------------
SCHEDULED_STATUS = "scheduled"

# Fields a caller may supply on create. patient_id / admission_id / procedure_type
# / scheduled_at come from the form; doctor_id is forced from the authenticated
# user; bed_id is resolved server-side from get_available_theatre() and can never
# be set by the caller.
_CREATE_FIELDS = ("patient_id", "admission_id", "procedure_type", "scheduled_at")
_REQUIRED_CREATE_FIELDS = _CREATE_FIELDS  # all four are mandatory


# ============================================================
# Small response helpers - keep every error/response body the same shape
# as the other blueprints in this service.
# ============================================================
def _error(message, status, **extra):
    """Return a ({"error": ...}, status) JSON tuple, plus any extra keys."""
    payload = {"error": message}
    payload.update(extra)
    return jsonify(payload), status


def _pick(body, fields):
    """Copy just `fields` out of `body` (dict), skipping keys that aren't present."""
    return {key: body[key] for key in fields if key in body}


def _current():
    """(role, user_id) for the acting user."""
    user = get_current_user() or {}
    return user.get("role"), user.get("id")


def _requests_for_admission(admission_id):
    """
    Every surgery_requests row whose admission_id matches, and nothing else.
    Strict filter - never returns another admission's rows.
    """
    all_rows = db.list_surgery_requests() or []
    try:
        wanted = int(admission_id)
    except (TypeError, ValueError):
        return []
    return [row for row in all_rows if row.get("admission_id") == wanted]


# ============================================================
# CREATE  ->  POST /api/surgery-requests/
# Restricted to the admission's assigned doctor. Blocked if the admission is
# not active. Blocked (nothing written) if no theatre is available. Creates the
# local row only after a bed_id is confirmed, then dispatches to Room & Bed and
# rolls the row back on any dispatch failure.
# ============================================================
@surgery_requests_bp.post("/")
@require_role("doctor")  # nurses / specialists / non-clinical roles rejected here
def create_surgery_request():
    role, user_id = _current()
    body = request.get_json(silent=True) or {}

    # --- basic payload validation -----------------------------------------
    missing = [f for f in _REQUIRED_CREATE_FIELDS if not body.get(f)]
    if missing:
        return _error("Missing required field(s): {}".format(", ".join(missing)), 400)

    data = _pick(body, _CREATE_FIELDS)

    # doctor_id is ALWAYS the authenticated doctor - a doctor_id in the body is
    # ignored. This makes "the assigned doctor" == "the doctor scheduling it".
    data["doctor_id"] = user_id

    # --- step 2: admission gate (same rule as clinical_records) ----------
    # This is the ONLY create-time block that comes before theatre selection.
    try:
        require_active_for_create(data["admission_id"])
    except AdmissionNotActiveError as exc:
        return _error(
            "Admission {} is '{}', not active - cannot schedule a surgery.".format(
                exc.admission_id, exc.admission_status
            ),
            409,
        )
    except AdmissionServiceError as exc:
        # Could not confirm the admission is active - fail safe, write nothing.
        return _error("Could not verify admission status: {}".format(exc), 502)

    # --- step 3: theatre selection --------------------------------------
    # notify_room_and_bed() must NOT be called until this returns a real bed_id.
    theatre = get_available_theatre()

    if not theatre.get("ok"):
        # The availability lookup itself failed (timeout / connection). The
        # system could not answer - 503, and nothing is written.
        return _error(
            "Room & Bed Management is unavailable - could not check theatre "
            "availability. The surgery request was not created; please try again "
            "shortly.",
            503,
            reason=theatre.get("error", "unavailable"),
        )

    bed_id = theatre.get("bed_id")
    if bed_id is None:
        # Call succeeded, but there is genuinely no theatre free
        # ({"ok": True, "bed_id": None, "reason": "none_available"}).
        # Per the reasoning above: a clean 409, NOTHING saved. Distinct from
        # the 503 above - the doctor should understand retrying later is right.
        return _error(
            "No surgical theatre is currently available. The surgery request was "
            "not created - please try again later or contact Room & Bed "
            "Management.",
            409,
            reason=theatre.get("reason", "none_available"),
        )

    # --- step 4: resolve the surgeon's NAME from doctor_id --------------
    # Room & Bed expects a name, not an id. Do this before the local write so a
    # bad/unreachable Staff & Shift service doesn't leave an orphan row behind.
    staff_result = get_staff_details(user_id)
    if not staff_result.get("ok"):
        # not_found -> the acting doctor_id isn't a known staff member (400-ish
        # config problem); unavailable -> Staff & Shift is down (503).
        if staff_result.get("error") == "not_found":
            return _error(
                "Scheduling doctor (id {}) is not a known staff member - cannot "
                "resolve a surgeon name for Room & Bed.".format(user_id),
                400,
            )
        return _error(
            "Staff & Shift Management is unavailable - could not resolve the "
            "surgeon's name. The surgery request was not created; please try "
            "again shortly.",
            503,
            reason=staff_result.get("error", "unavailable"),
        )

    surgeon_name = (staff_result.get("staff") or {}).get("full_name")
    if not surgeon_name:
        # Staff record exists but carries no usable name - don't dispatch a
        # nameless surgeon to Room & Bed.
        return _error(
            "Staff record for doctor id {} has no name - cannot dispatch to "
            "Room & Bed.".format(user_id),
            502,
        )

    # --- step 5: create the local row (bed_id is now CONFIRMED) --------
    # This is the first and only local write. bed_id is a real theatre bed,
    # satisfying the schema's NOT NULL constraint and the "only create once a
    # bed_id is confirmed" rule.
    row_to_create = {
        "patient_id": data["patient_id"],
        "admission_id": data["admission_id"],
        "doctor_id": user_id,                 # from auth, never the body
        "bed_id": bed_id,                     # resolved by get_available_theatre()
        "procedure_type": data["procedure_type"],
        "scheduled_at": data["scheduled_at"],
        "status": SCHEDULED_STATUS,           # always starts here
    }

    try:
        created = db.create_surgery_request(row_to_create)
    except db.DatabaseClientError as exc:
        return _error("Could not create the surgery request: {}".format(exc), 502)

    created_id = created.get("request_id")

    # --- step 6: dispatch to Room & Bed -------------------------------
    # Build the surgery_request dict notify_room_and_bed() expects. Pass the
    # created row (it has everything) so procedure_type / patient_id /
    # admission_id / scheduled_at all line up with what was persisted.
    dispatch_result = notify_room_and_bed(created, surgeon_name, bed_id)

    if dispatch_result.get("ok"):
        # Room & Bed accepted the arrangement - the local row stays.
        return jsonify(
            surgery_request=created,
            room_and_bed=dispatch_result.get("arrangement"),
        ), 201

    # ---- dispatch failed: roll the local row back, then branch on WHY ----
    # Any failure here means Room & Bed did not confirm acceptance, so the row
    # must not survive. Attempt the delete regardless of the failure kind.
    rollback_note = _rollback_created_row(created_id)

    error_kind = dispatch_result.get("error")

    if error_kind == "refused":
        # DISTINCT from a transport failure: HTTP 409 + success:false. A real,
        # definitive refusal (clash / maintenance / out of service). Surface the
        # detail verbatim so the doctor knows what to change.
        return _error(
            "Room & Bed Management refused the theatre booking. The surgery "
            "request was not saved - please choose another time or procedure "
            "and resubmit.",
            409,
            reason="refused",
            room_and_bed_detail=dispatch_result.get("detail"),
            rollback=rollback_note,
        )

    if error_kind == "unavailable":
        # DISTINCT from a refusal: timeout / connection failure. Outcome is
        # UNKNOWN - Room & Bed may or may not have recorded the arrangement.
        return _error(
            "Could not confirm the surgery request with Room & Bed Management "
            "(service unavailable). It has NOT been saved here. If you resubmit "
            "and later see a duplicate theatre booking, contact Room & Bed "
            "Management.",
            502,
            reason="unavailable",
            room_and_bed_detail=dispatch_result.get("detail"),
            rollback=rollback_note,
        )

    # Defensive: an error shape external_services.py doesn't currently produce.
    # Still roll back (done above) and report it rather than 500.
    return _error(
        "Room & Bed Management dispatch failed for an unrecognised reason. The "
        "surgery request was not saved.",
        502,
        reason=error_kind or "unknown",
        room_and_bed_detail=dispatch_result.get("detail"),
        rollback=rollback_note,
    )


def _rollback_created_row(created_id):
    """
    Delete the surgery_requests row we created just before a failed dispatch.

    Returns a short human-readable note about how the rollback went, which the
    caller folds into its error response. A rollback that itself fails is
    reported (so a human can clean up) but does not change the HTTP status -
    the dispatch failure is still the primary error.
    """
    if created_id is None:
        return "no row id to roll back"
    try:
        db.delete_surgery_request(created_id)
        return "local surgery_requests row {} removed".format(created_id)
    except db.DatabaseClientError as exc:
        # The row is stranded as 'scheduled'. It was never confirmed with
        # Room & Bed, so this needs manual cleanup - make that visible.
        return (
            "WARNING: could not remove local surgery_requests row {} after a "
            "failed dispatch - manual cleanup required ({})".format(created_id, exc)
        )


# ============================================================
# ADMISSION-SCOPED LIST  ->  GET /api/surgery-requests/admission/<admission_id>
# Every surgery request raised for one admission. Strict admission_id filter -
# never mixes in another admission's rows.
# ============================================================
@surgery_requests_bp.get("/admission/<int:admission_id>")
@require_role("doctor", "nurse", "specialist")
def get_admission_surgery_requests(admission_id):
    try:
        rows = _requests_for_admission(admission_id)
    except db.DatabaseClientError as exc:
        return _error("Could not reach the surgery-requests database: {}".format(exc), 502)

    # Defence in depth: re-assert the scope right before responding, so a bug in
    # the filter can never leak a foreign admission's row.
    rows = [r for r in rows if r.get("admission_id") == admission_id]

    return jsonify(admission_id=admission_id, surgery_requests=rows), 200


# ------------------------------------------------------------
# Blueprint-local error handlers - keep this blueprint correct on its own even
# if app.py doesn't register global ones. Mirrors clinical_records.py.
# ------------------------------------------------------------
@surgery_requests_bp.errorhandler(AuthError)
def _handle_auth_error(err):
    return jsonify(error=err.message), err.status


@surgery_requests_bp.errorhandler(db.DatabaseClientError)
def _handle_db_error(err):
    # Catches any DatabaseClientError raised outside a route's own try/except
    # (e.g. inside a helper), so it surfaces as 502 rather than a 500 traceback.
    return _error("Could not reach the surgery-requests database: {}".format(err), 502)
