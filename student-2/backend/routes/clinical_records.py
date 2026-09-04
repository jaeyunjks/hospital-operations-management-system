"""
clinical_records.py - Flask Blueprint for the clinical_records table.

Endpoints (mounted at /api/clinical-records by app.py):
    GET    /                              -> list records the caller may see
    POST   /                              -> create a record (blocked if admission inactive)
    GET    /<record_id>                   -> fetch one record
    PUT    /<record_id>                   -> update a record (never blocked; flags post-discharge edits)
    DELETE /<record_id>                   -> soft-delete (status -> 'archived', row kept)
    GET    /admission/<admission_id>      -> full clinical history for ONE admission

Rules enforced here (see schema.sql header):
  * POST is blocked (409) only if the referenced admission is no longer active.
  * PUT is always allowed; if the admission is inactive at update time we set
    updated_after_discharge = 1 on that record rather than refusing.
  * DELETE never removes the row - it sets status = 'archived'.
  * GET /admission/<id> is the only admission-scoped history endpoint. It filters
    strictly by admission_id and never returns a record from another admission.
    The AI summary feature consumes this endpoint.

Access control (via auth.py):
  * Doctor / Nurse: may act on records for patients they are assigned to.
      - Doctor is assigned when clinical_records.doctor_id == their id.
      - Nurse is assigned when they own a care_task on that record.
  * Specialist: READ-ONLY, and only when a consultation_requests row links them
    (specialist_id) to the record. Specialists never reach a record directly.

Every write goes through services/database_client.py - no raw SQL in this file.
"""

from flask import Blueprint, jsonify, request

from auth import (
    AuthError,
    get_current_user,
    require_role,
)
from services import database_client as db
from services.admission_validation import (
    AdmissionNotActiveError,
    AdmissionServiceError,
    require_active_for_create,
)

# Statuses this service uses. 'archived' is the soft-delete sink.
ARCHIVED_STATUS = "archived"

# Fields a caller is allowed to set on create / update. Anything else in the
# request body is ignored so callers cannot, e.g., forge updated_after_discharge.
CREATE_FIELDS = (
    "patient_id",
    "admission_id",
    "doctor_id",
    "assessment_notes",
    "diagnosis_summary",
    "care_plan",
    "status",
)
UPDATE_FIELDS = (
    "assessment_notes",
    "diagnosis_summary",
    "care_plan",
    "status",
)
REQUIRED_CREATE_FIELDS = ("patient_id", "admission_id", "doctor_id", "assessment_notes")

clinical_records_bp = Blueprint("clinical_records", __name__)


# ============================================================
# Small response helpers - keep every error body the same shape.
# ============================================================
def _error(message, status):
    """Return a ({"error": ...}, status) tuple for a JSON error response."""
    return jsonify(error=message), status


def _pick(body, fields):
    """Copy just `fields` out of `body` (dict), skipping keys that aren't present."""
    return {key: body[key] for key in fields if key in body}


# ============================================================
# Data-access wrappers around database_client, with 404 mapping.
# ============================================================
def _load_record_or_none(record_id):
    """Fetch one clinical record, or None if the database API says it's missing."""
    try:
        return db.get_clinical_record(record_id)
    except db.DatabaseClientError as exc:
        # 404 from the database API means "no such row"; anything else is a
        # real fault the caller should hear about.
        if exc.status_code == 404:
            return None
        raise


def _records_for_admission(admission_id):
    """
    Return every clinical_records row whose admission_id matches, and nothing
    else. This is the strict filter that guarantees no cross-admission leakage.
    """
    all_rows = db.list_clinical_records() or []
    # Compare as int so "3" and 3 match; ignore rows with an unexpected shape.
    try:
        wanted = int(admission_id)
    except (TypeError, ValueError):
        return []
    return [row for row in all_rows if row.get("admission_id") == wanted]


def _care_tasks_for_record(record_id):
    """Nurse ids with a care_task on this record (used for nurse assignment)."""
    all_tasks = db.list_care_tasks() or []
    return [t for t in all_tasks if t.get("clinical_record_id") == record_id]


def _consults_for_record(record_id):
    """Consultation rows linking a specialist to this record (specialist access)."""
    all_consults = db.list_consultation_requests() or []
    return [c for c in all_consults if c.get("clinical_record_id") == record_id]


# ============================================================
# Access checks - role + assignment, tuned to this table.
# require_role() (from auth.py) already rejects non-clinical roles; these
# helpers add the "are you connected to THIS record?" rule.
# ============================================================
def _assert_can_read_record(record):
    """
    Raise AuthError unless the current user may read `record`.

    Doctor/Nurse: must be assigned (doctor_id, or an owned care_task).
    Specialist:   must have a consultation_requests link to the record.
    """
    user = get_current_user() or {}
    role = user.get("role")
    user_id = user.get("id")

    if role == "doctor":
        if record.get("doctor_id") == user_id:
            return
        raise AuthError("You are not the doctor assigned to this record.", status=403)

    if role == "nurse":
        nurse_ids = {t.get("assigned_nurse_id") for t in _care_tasks_for_record(record["record_id"])}
        if user_id in nurse_ids:
            return
        raise AuthError("You have no care task on this record.", status=403)

    if role == "specialist":
        specialist_ids = {c.get("specialist_id") for c in _consults_for_record(record["record_id"])}
        if user_id in specialist_ids:
            return
        raise AuthError(
            "Specialists can only view records they hold a consultation for.",
            status=403,
        )

    # require_role already blocks this, but stay defensive.
    raise AuthError("Your role may not access clinical records.", status=403)


def _assert_can_write_record(record):
    """
    Raise AuthError unless the current user may modify `record`.

    Only the assigned doctor or an assigned nurse may write. Specialists are
    read-only on this table - their input flows through consultation_requests.
    """
    user = get_current_user() or {}
    role = user.get("role")

    if role == "specialist":
        raise AuthError("Specialists have read-only access to clinical records.", status=403)

    # Reuse the read check for doctor/nurse assignment; it already raises 403.
    _assert_can_read_record(record)


# ============================================================
# LIST  ->  GET /api/clinical-records/
# Returns only the (non-archived) records the caller is entitled to see.
# ============================================================
@clinical_records_bp.get("/")
@require_role("doctor", "nurse", "specialist")
def list_records():
    try:
        rows = db.list_clinical_records() or []
    except db.DatabaseClientError as exc:
        return _error("Could not reach the records database: {}".format(exc), 502)

    visible = []
    for row in rows:
        # Silently skip anything the caller isn't connected to.
        try:
            _assert_can_read_record(row)
        except AuthError:
            continue
        visible.append(row)

    return jsonify(clinical_records=visible), 200


# ============================================================
# CREATE  ->  POST /api/clinical-records/
# Blocked with 409 only when the admission is no longer active.
# ============================================================
@clinical_records_bp.post("/")
@require_role("doctor", "nurse")
def create_record():
    body = request.get_json(silent=True) or {}

    # Basic payload validation.
    missing = [f for f in REQUIRED_CREATE_FIELDS if not body.get(f)]
    if missing:
        return _error("Missing required field(s): {}".format(", ".join(missing)), 400)

    data = _pick(body, CREATE_FIELDS)

    # HTML number inputs arrive through the frontend as JSON strings. Normalise
    # identifiers before authorization and before forwarding them to the
    # database API so the browser and direct JSON clients follow the same path.
    try:
        for field in ("patient_id", "admission_id", "doctor_id"):
            data[field] = int(data[field])
    except (TypeError, ValueError):
        return _error("patient_id, admission_id and doctor_id must be integers.", 400)

    # A doctor may only file records under their own doctor_id; a nurse-created
    # record must still name the assigned doctor.
    user = get_current_user() or {}
    if user.get("role") == "doctor" and data.get("doctor_id") != user.get("id"):
        return _error("A doctor can only create records under their own doctor_id.", 403)

    # Admission gate: this is the ONLY create-time block.
    try:
        require_active_for_create(data["admission_id"])
    except AdmissionNotActiveError as exc:
        return _error(
            "Admission {} is '{}', not active - cannot create a clinical record.".format(
                exc.admission_id, exc.admission_status
            ),
            409,
        )
    except AdmissionServiceError as exc:
        # We could not confirm the admission is active - fail safe, don't create.
        return _error("Could not verify admission status: {}".format(exc), 502)

    # Never let the caller pre-set the audit flag.
    data.pop("updated_after_discharge", None)

    try:
        created = db.create_clinical_record(data)
    except db.DatabaseClientError as exc:
        return _error("Could not create the clinical record: {}".format(exc), 502)

    return jsonify(clinical_record=created), 201


# ============================================================
# READ ONE  ->  GET /api/clinical-records/<record_id>
# ============================================================
@clinical_records_bp.get("/<int:record_id>")
@require_role("doctor", "nurse", "specialist")
def get_record(record_id):
    try:
        record = _load_record_or_none(record_id)
    except db.DatabaseClientError as exc:
        return _error("Could not reach the records database: {}".format(exc), 502)

    if record is None:
        return _error("No clinical record with id {}.".format(record_id), 404)

    _assert_can_read_record(record)  # raises AuthError (403) if not entitled
    return jsonify(clinical_record=record), 200


# ============================================================
# UPDATE  ->  PUT /api/clinical-records/<record_id>
# Always allowed for an assigned writer. If the admission is no longer active
# at update time, we set updated_after_discharge = 1 instead of blocking.
# ============================================================
@clinical_records_bp.put("/<int:record_id>")
@require_role("doctor", "nurse")
def update_record(record_id):
    body = request.get_json(silent=True) or {}

    try:
        record = _load_record_or_none(record_id)
    except db.DatabaseClientError as exc:
        return _error("Could not reach the records database: {}".format(exc), 502)

    if record is None:
        return _error("No clinical record with id {}.".format(record_id), 404)

    _assert_can_write_record(record)  # raises AuthError (403) if not entitled

    data = _pick(body, UPDATE_FIELDS)
    if not data:
        return _error("No updatable fields supplied.", 400)

    # Post-discharge audit flag: check the admission, but never block the update.
    try:
        require_active_for_create(record["admission_id"])
    except AdmissionNotActiveError:
        # Admission has ended - stamp the audit flag so the UI can show a banner.
        data["updated_after_discharge"] = 1
    except AdmissionServiceError as exc:
        # Status unknown. The rule says PUT is always allowed, so proceed, but
        # be conservative and flag it rather than silently treating it as active.
        data["updated_after_discharge"] = 1
        # (No early return - the update still goes through below.)
        _ = exc

    try:
        updated = db.update_clinical_record(record_id, data)
    except db.DatabaseClientError as exc:
        return _error("Could not update the clinical record: {}".format(exc), 502)

    return jsonify(clinical_record=updated), 200


# ============================================================
# DELETE  ->  DELETE /api/clinical-records/<record_id>
# Soft delete: set status -> 'archived' via database_client. Row is kept.
# ============================================================
@clinical_records_bp.delete("/<int:record_id>")
@require_role("doctor", "nurse")
def delete_record(record_id):
    try:
        record = _load_record_or_none(record_id)
    except db.DatabaseClientError as exc:
        return _error("Could not reach the records database: {}".format(exc), 502)

    if record is None:
        return _error("No clinical record with id {}.".format(record_id), 404)

    _assert_can_write_record(record)  # raises AuthError (403) if not entitled

    # Already archived - nothing to do, report it plainly.
    if record.get("status") == ARCHIVED_STATUS:
        return jsonify(clinical_record=record, note="already archived"), 200

    # Soft delete = an update that flips status. We do NOT call delete_clinical_record.
    try:
        archived = db.update_clinical_record(record_id, {"status": ARCHIVED_STATUS})
    except db.DatabaseClientError as exc:
        return _error("Could not archive the clinical record: {}".format(exc), 502)

    return jsonify(clinical_record=archived, note="archived (soft delete)"), 200


# ============================================================
# ADMISSION-SCOPED HISTORY  ->  GET /api/clinical-records/admission/<admission_id>
# The single supported way to pull one admission's clinical history.
# Filters strictly by admission_id; never mixes in another admission's rows.
# This is the endpoint the AI summary feature will call.
# ============================================================
@clinical_records_bp.get("/admission/<int:admission_id>")
@require_role("doctor", "nurse", "specialist")
def get_admission_history(admission_id):
    try:
        rows = _records_for_admission(admission_id)
    except db.DatabaseClientError as exc:
        return _error("Could not reach the records database: {}".format(exc), 502)

    # Entitlement is decided per record (doctor assignment / nurse task /
    # specialist consult). A caller with no connection to any record on this
    # admission gets an empty history, not someone else's data.
    visible = []
    for row in rows:
        try:
            _assert_can_read_record(row)
        except AuthError:
            continue
        visible.append(row)

    # Defence in depth: re-assert the scope right before responding, so a bug
    # in the filter above can never leak a foreign admission's record.
    visible = [r for r in visible if r.get("admission_id") == admission_id]

    if not rows:
        # Distinguish "no such admission / no records" from "found but hidden".
        return jsonify(admission_id=admission_id, clinical_records=[]), 200

    return jsonify(admission_id=admission_id, clinical_records=visible), 200


# ============================================================
# Blueprint-local error handler for auth failures.
# app.py may also register a global one; this keeps the blueprint correct
# on its own. 403 for auth failures, 404 for "record not found" from
# require_assignment-style lookups.
# ============================================================
@clinical_records_bp.errorhandler(AuthError)
def _handle_auth_error(err):
    return jsonify(error=err.message), err.status

# 
@clinical_records_bp.errorhandler(db.DatabaseClientError)
def _handle_db_error(err):
    # _assert_can_read_record / _assert_can_write_record call db.list_care_tasks /
    # db.list_consultation_requests (for nurse / specialist callers) outside the
    # per-handler try/except. Without this, a database-API fault there surfaces as
    # 500 instead of the 502 every explicit handler already returns.
    return _error("Could not reach the records database: {}".format(err), 502)
