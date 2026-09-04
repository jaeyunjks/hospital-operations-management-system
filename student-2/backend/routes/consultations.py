"""
consultations.py - Flask Blueprint for the consultation_requests table.

Mounted at /api/consultations by app.py.

    GET    /                     list requests the caller is party to
    GET    /<request_id>         view one request (specialist's first view -> 'in_review')
    POST   /                     doctor raises a request against a clinical record
    PUT    /<request_id>         doctor edits the reason while still 'requested'
    DELETE /<request_id>         doctor soft-cancels a request still 'requested'
    PUT    /<request_id>/respond specialist records a recommendation -> 'completed'

Roles: doctors and specialists only (auth.require_role). Nurses and everyone
else are rejected on every route.

Ownership:
  * A doctor sees / edits / cancels only requests where requesting_doctor_id is
    their own id. requesting_doctor_id is always taken from the current user on
    create - a doctor_id in the request body is ignored.
  * A specialist sees / responds to only requests where specialist_id is theirs.

Status lifecycle (schema.sql, table 2):
    requested --(specialist opens it)--> in_review --(specialist responds)--> completed
    requested --(doctor cancels)--------> cancelled
    requested / in_review --(admission went inactive)--> cancelled  (automatic)

Read-only rules:
  * Once submitted, the request is read-only for the doctor except for DELETE
    (cancel) while it is still 'requested'.
  * Once 'completed' or 'cancelled', the request is read-only for everyone.

Admission rule: every access to an open request runs cancel_if_inactive() FIRST.
If the linked admission is no longer active the request is auto-cancelled and the
caller is told so - it is never answered with an error for that reason. Creating
a request against a non-active admission is refused by cancelling it up front.

All writes go through services.database_client - no raw SQL here.
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from auth import AuthError, get_current_user, require_role
from services import database_client as db
from services.admission_validation import AdmissionServiceError, cancel_if_inactive

consultations_bp = Blueprint("consultations", __name__)

# ------------------------------------------------------------
# Status constants + groupings.
# ------------------------------------------------------------
REQUESTED = "requested"
IN_REVIEW = "in_review"
COMPLETED = "completed"
CANCELLED = "cancelled"

# "Open" = still moving through the workflow; these are the ones the admission
# auto-cancel check applies to.
OPEN_STATES = {REQUESTED, IN_REVIEW}
# "Closed" = terminal; read-only for everyone.
CLOSED_STATES = {COMPLETED, CANCELLED}

# Only these two roles ever touch this service.
_ROLES = ("doctor", "specialist")

# Fields a doctor may supply when creating. requesting_doctor_id is NOT here -
# it is set from the authenticated user. specialist_id / reason come from the form.
_CREATE_FIELDS = ("clinical_record_id", "specialist_id", "reason_for_request")
_CREATE_REQUIRED = _CREATE_FIELDS


# ============================================================
# Helpers
# ============================================================
def _err(message, code):
    """Uniform JSON error body + status code."""
    return jsonify({"error": message}), code


def _now_iso():
    """UTC ISO-8601 timestamp with a trailing Z, for completed_at."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch(request_id):
    """Return the consultation row, or None if the database API 404s it."""
    try:
        return db.get_consultation_request(request_id)
    except db.DatabaseClientError as exc:
        if exc.status_code == 404:
            return None
        raise


def _current():
    """(role, user_id) for the acting user."""
    user = get_current_user() or {}
    return user.get("role"), user.get("id")


def _is_party_to(row, role, user_id):
    """
    True if this user is the request's own doctor or its own specialist.
    A doctor is party via requesting_doctor_id; a specialist via specialist_id.
    """
    if role == "doctor":
        return row.get("requesting_doctor_id") == user_id
    if role == "specialist":
        return row.get("specialist_id") == user_id
    return False


def _auto_cancel_if_inactive(row):
    """
    Run the admission check FIRST on any open request. If the linked admission
    is no longer active, soft-cancel the request and return the updated row.
    Otherwise return the row unchanged.

    Raises AdmissionServiceError if the admission status cannot be fetched -
    an unknown status is not treated as "inactive".
    """
    if row.get("status") not in OPEN_STATES:
        return row  # already closed - nothing to auto-cancel

    if not cancel_if_inactive(row["admission_id"]):
        return row  # admission still active - leave it alone

    # Admission has gone inactive: auto-cancel, do not error.
    updated = db.update_consultation_request(row["request_id"], {"status": CANCELLED})
    return updated


# ============================================================
# LIST  ->  GET /api/consultations/
# Doctor: their own requests. Specialist: requests addressed to them.
# ============================================================
@consultations_bp.get("/")
@require_role(*_ROLES)
def list_requests():
    role, user_id = _current()
    try:
        rows = db.list_consultation_requests() or []
    except db.DatabaseClientError as exc:
        return _err("consultation lookup failed: {}".format(exc), 502)

    # Filter to just the caller's own requests before returning anything.
    mine = [r for r in rows if _is_party_to(r, role, user_id)]

    # Optional status filter, e.g. a specialist's "pending" queue.
    wanted_status = request.args.get("status")
    if wanted_status:
        mine = [r for r in mine if r.get("status") == wanted_status]

    return jsonify({"consultation_requests": mine}), 200


# ============================================================
# READ ONE  ->  GET /api/consultations/<request_id>
# Admission auto-cancel runs first. A specialist's FIRST view of a 'requested'
# request moves it to 'in_review'.
# ============================================================
@consultations_bp.get("/<int:request_id>")
@require_role(*_ROLES)
def get_request(request_id):
    role, user_id = _current()

    try:
        row = _fetch(request_id)
    except db.DatabaseClientError as exc:
        return _err("consultation lookup failed: {}".format(exc), 502)
    if row is None:
        return _err("consultation request {} not found".format(request_id), 404)

    # Only the request's own doctor / specialist may see it.
    if not _is_party_to(row, role, user_id):
        return _err("this consultation request is not yours", 403)

    # Admission check FIRST - may flip an open request to 'cancelled'.
    try:
        row = _auto_cancel_if_inactive(row)
    except AdmissionServiceError as exc:
        return _err("could not verify admission status: {}".format(exc), 502)
    except db.DatabaseClientError as exc:
        return _err("could not auto-cancel stale request: {}".format(exc), 502)

    # Specialist opening a still-'requested' request for the first time:
    # transition it to 'in_review' so the doctor can see it's been picked up.
    if role == "specialist" and row.get("status") == REQUESTED:
        try:
            row = db.update_consultation_request(request_id, {"status": IN_REVIEW})
        except db.DatabaseClientError as exc:
            return _err("could not move request to in_review: {}".format(exc), 502)

    return jsonify({"consultation_request": row}), 200


# ============================================================
# CREATE  ->  POST /api/consultations/
# Doctor only. requesting_doctor_id is forced from the current user.
# The linked admission must be active or the request is cancelled immediately.
# ============================================================
@consultations_bp.post("/")
@require_role("doctor")
def create_request():
    _role, user_id = _current()
    body = request.get_json(silent=True) or {}

    # Validate the form fields we accept.
    missing = [f for f in _CREATE_REQUIRED if not body.get(f)]
    if missing:
        return _err("missing required field(s): {}".format(", ".join(missing)), 400)

    # The clinical record must exist and belong to this doctor - a consult is
    # raised from a record the doctor is working on.
    try:
        record = db.get_clinical_record(body["clinical_record_id"])
    except db.DatabaseClientError as exc:
        if exc.status_code == 404:
            return _err(
                "clinical record {} does not exist".format(body["clinical_record_id"]), 404
            )
        return _err("could not verify clinical record: {}".format(exc), 502)

    if record.get("doctor_id") != user_id:
        return _err("you can only raise a consultation from your own clinical record", 403)

    # Build the payload. requesting_doctor_id comes from auth, NEVER the body.
    payload = {
        "clinical_record_id": body["clinical_record_id"],
        "patient_id": record.get("patient_id"),
        "admission_id": record.get("admission_id"),
        "requesting_doctor_id": user_id,           # from current user, not the body
        "specialist_id": body["specialist_id"],
        "reason_for_request": body["reason_for_request"],
        "status": REQUESTED,                       # always starts here
    }

    # The admission must be active right now. Reuse cancel_if_inactive: if it
    # says "should cancel", the admission is not active -> refuse the create.
    try:
        if cancel_if_inactive(payload["admission_id"]):
            return _err(
                "admission {} is not active - consultation request refused".format(
                    payload["admission_id"]
                ),
                409,
            )
    except AdmissionServiceError as exc:
        return _err("could not verify admission status: {}".format(exc), 502)

    try:
        created = db.create_consultation_request(payload)
    except db.DatabaseClientError as exc:
        return _err("could not create consultation request: {}".format(exc), 502)

    return jsonify({"consultation_request": created}), 201


# ============================================================
# EDIT  ->  PUT /api/consultations/<request_id>
# The doctor may still adjust the reason while the request is 'requested'.
# Once a specialist has it (in_review) or it is closed, it is read-only.
# ============================================================
@consultations_bp.put("/<int:request_id>")
@require_role("doctor")
def edit_request(request_id):
    _role, user_id = _current()

    try:
        row = _fetch(request_id)
    except db.DatabaseClientError as exc:
        return _err("consultation lookup failed: {}".format(exc), 502)
    if row is None:
        return _err("consultation request {} not found".format(request_id), 404)

    # Must be this doctor's own request.
    if row.get("requesting_doctor_id") != user_id:
        return _err("this consultation request is not yours", 403)

    # Admission check first - might auto-cancel it out from under the edit.
    try:
        row = _auto_cancel_if_inactive(row)
    except AdmissionServiceError as exc:
        return _err("could not verify admission status: {}".format(exc), 502)
    except db.DatabaseClientError as exc:
        return _err("could not auto-cancel stale request: {}".format(exc), 502)

    # Read-only once the specialist has picked it up or it has closed.
    if row.get("status") != REQUESTED:
        return _err(
            "request is '{}' and can no longer be edited by the doctor".format(
                row.get("status")
            ),
            409,
        )

    body = request.get_json(silent=True) or {}
    if not body.get("reason_for_request"):
        return _err("only 'reason_for_request' can be edited, and it must be non-empty", 400)

    try:
        updated = db.update_consultation_request(
            request_id, {"reason_for_request": body["reason_for_request"]}
        )
    except db.DatabaseClientError as exc:
        return _err("could not update consultation request: {}".format(exc), 502)

    return jsonify({"consultation_request": updated}), 200


# ============================================================
# CANCEL (soft delete)  ->  DELETE /api/consultations/<request_id>
# Doctor only, and only while the request is still 'requested'. Row is kept.
# ============================================================
@consultations_bp.delete("/<int:request_id>")
@require_role("doctor")
def cancel_request(request_id):
    _role, user_id = _current()

    try:
        row = _fetch(request_id)
    except db.DatabaseClientError as exc:
        return _err("consultation lookup failed: {}".format(exc), 502)
    if row is None:
        return _err("consultation request {} not found".format(request_id), 404)

    if row.get("requesting_doctor_id") != user_id:
        return _err("this consultation request is not yours", 403)

    # Admission check first - it may already have auto-cancelled the request,
    # in which case we just report that state.
    try:
        row = _auto_cancel_if_inactive(row)
    except AdmissionServiceError as exc:
        return _err("could not verify admission status: {}".format(exc), 502)
    except db.DatabaseClientError as exc:
        return _err("could not auto-cancel stale request: {}".format(exc), 502)

    if row.get("status") == CANCELLED:
        return jsonify({"consultation_request": row, "note": "already cancelled"}), 200

    # A doctor may only withdraw a request that no specialist has touched yet.
    if row.get("status") != REQUESTED:
        return _err(
            "a doctor can only cancel a request that is still 'requested' "
            "(this one is '{}')".format(row.get("status")),
            409,
        )

    try:
        cancelled = db.update_consultation_request(request_id, {"status": CANCELLED})
    except db.DatabaseClientError as exc:
        return _err("could not cancel consultation request: {}".format(exc), 502)

    return jsonify({"consultation_request": cancelled, "note": "cancelled (soft delete)"}), 200


# ============================================================
# RESPOND  ->  PUT /api/consultations/<request_id>/respond
# Specialist records a recommendation and closes the request as 'completed',
# stamping completed_at. Only the addressed specialist; only on an open request.
# ============================================================
@consultations_bp.put("/<int:request_id>/respond")
@require_role("specialist")
def respond_to_request(request_id):
    _role, user_id = _current()
    body = request.get_json(silent=True) or {}

    recommendation = body.get("recommendation")
    if not recommendation:
        return _err("'recommendation' is required to respond", 400)

    try:
        row = _fetch(request_id)
    except db.DatabaseClientError as exc:
        return _err("consultation lookup failed: {}".format(exc), 502)
    if row is None:
        return _err("consultation request {} not found".format(request_id), 404)

    # Only the specialist the request was addressed to may respond.
    if row.get("specialist_id") != user_id:
        return _err("this consultation request was not addressed to you", 403)

    # Admission check first - a discharge auto-cancels the request instead of
    # letting a recommendation land on a stale admission.
    try:
        row = _auto_cancel_if_inactive(row)
    except AdmissionServiceError as exc:
        return _err("could not verify admission status: {}".format(exc), 502)
    except db.DatabaseClientError as exc:
        return _err("could not auto-cancel stale request: {}".format(exc), 502)

    # Closed (completed or auto-cancelled) -> read-only, cannot respond.
    if row.get("status") in CLOSED_STATES:
        return _err(
            "request is '{}' and can no longer be responded to".format(row.get("status")),
            409,
        )

    # Record the recommendation, mark complete, stamp the time.
    changes = {
        "recommendation": recommendation,
        "status": COMPLETED,
        "completed_at": _now_iso(),
    }
    try:
        completed = db.update_consultation_request(request_id, changes)
    except db.DatabaseClientError as exc:
        return _err("could not submit recommendation: {}".format(exc), 502)

    return jsonify({"consultation_request": completed}), 200


# ------------------------------------------------------------
# Role failures from require_role -> uniform JSON.
# ------------------------------------------------------------
@consultations_bp.errorhandler(AuthError)
def _on_auth_error(err):
    return jsonify({"error": err.message}), err.status
