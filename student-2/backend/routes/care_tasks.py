"""
care_tasks.py - Flask Blueprint for the care_tasks table.

Mounted at /api/care-tasks by app.py.

    GET    /                      list care tasks
    GET    /<task_id>             one care task
    POST   /                      doctor creates a task against a clinical record
    PUT    /<task_id>             edit description / notes / due_at (author-side)
    DELETE /<task_id>             soft-delete: status -> 'cancelled' (doctor or nurse)
    PUT    /<task_id>/acknowledge nurse picks the task up   (pending -> acknowledged)
    PUT    /<task_id>/complete    nurse closes the task out  (-> completed, stamps time)

Workflow (schema.sql, table 3):
  * New tasks are always born 'pending' - the client cannot choose the status.
  * 'acknowledge' and 'complete' are the assigned nurse's alone. They are wired
    to auth.require_assignment(), which looks up care_tasks.assigned_nurse_id and
    compares it to the caller - so the decorator, not this code, enforces it.
  * 'cancel' (via DELETE) is a shared doctor/nurse privilege for a task raised in
    error or no longer needed. The row is kept.
  * There is deliberately NO admission-status check anywhere in this file:
    completing a task after discharge is allowed and just closes it out.

Care tasks are a doctor <-> nurse workflow only. Specialists have NO access to
this table in any form - every route is restricted to doctor / nurse, never the
bare @require_role() that would also admit a specialist.

Writes go through services.database_client only - no raw SQL here.
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from auth import AuthError, get_current_user, require_assignment, require_role
from services import database_client as db

care_tasks_bp = Blueprint("care_tasks", __name__)

# ------------------------------------------------------------
# Status model. Kept as data so the transition rules read at a glance.
# ------------------------------------------------------------
PENDING = "pending"
ACKNOWLEDGED = "acknowledged"
COMPLETED = "completed"
CANCELLED = "cancelled"

# A task is "live" (still actionable) only in these states.
OPEN_STATES = {PENDING, ACKNOWLEDGED}

# Which starting states each nurse action accepts.
CAN_ACKNOWLEDGE_FROM = {PENDING}
CAN_COMPLETE_FROM = {PENDING, ACKNOWLEDGED}  # allow completing without a separate ack

# Every route in this file is for these two roles only. Specialists are never
# allowed near a care task, so this tuple is used instead of a bare require_role().
_TASK_ROLES = ("doctor", "nurse")

# Body fields the doctor supplies at creation. status/completed_at are set here.
_CREATE_FIELDS = ("clinical_record_id", "assigned_nurse_id", "task_description", "due_at")
_CREATE_REQUIRED = ("clinical_record_id", "assigned_nurse_id", "task_description")

# Body fields a plain PUT may touch. Status changes go through the action routes.
_EDIT_FIELDS = ("task_description", "notes", "due_at")


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _json_error(message, code):
    """Uniform JSON error body."""
    return jsonify({"error": message}), code


def _now_iso():
    """UTC timestamp for completed_at, ISO-8601 with a trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_task(task_id):
    """
    Return the care_task dict, or None if the database API reports it missing.
    Any other database fault propagates as DatabaseClientError.
    """
    try:
        return db.get_care_task(task_id)
    except db.DatabaseClientError as exc:
        if exc.status_code == 404:
            return None
        raise


def _assigned_nurse_id(task_id, **_kwargs):
    """
    Lookup for auth.require_assignment: given the task id, return the staff id
    the task belongs to (its assigned_nurse_id), or None if the task is gone.
    require_assignment turns None into a 404 and a mismatch into a 403.
    """
    task = _fetch_task(task_id)
    return task.get("assigned_nurse_id") if task else None


# ============================================================
# Reads - doctor or nurse only. A specialist calling these gets 403 from
# require_role("doctor", "nurse") before any handler code runs.
# ============================================================
@care_tasks_bp.get("/")
@require_role(*_TASK_ROLES)
def list_tasks():
    """List care tasks, optionally narrowed by ?nurse_id= or ?clinical_record_id=."""
    try:
        tasks = db.list_care_tasks() or []
    except db.DatabaseClientError as exc:
        return _json_error("care task lookup failed: {}".format(exc), 502)

    # Optional query-string filters - handy for a nurse's "my tasks" list.
    nurse_id = request.args.get("nurse_id", type=int)
    record_id = request.args.get("clinical_record_id", type=int)
    if nurse_id is not None:
        tasks = [t for t in tasks if t.get("assigned_nurse_id") == nurse_id]
    if record_id is not None:
        tasks = [t for t in tasks if t.get("clinical_record_id") == record_id]

    return jsonify({"care_tasks": tasks}), 200


@care_tasks_bp.get("/<int:task_id>")
@require_role(*_TASK_ROLES)
def get_task(task_id):
    """Fetch a single care task."""
    try:
        task = _fetch_task(task_id)
    except db.DatabaseClientError as exc:
        return _json_error("care task lookup failed: {}".format(exc), 502)
    if task is None:
        return _json_error("care task {} not found".format(task_id), 404)
    return jsonify({"care_task": task}), 200


# ============================================================
# Create - a doctor attaches a task to an existing clinical record.
# Status is forced to 'pending'; the nurse's list picks it up from there.
# ============================================================
@care_tasks_bp.post("/")
@require_role("doctor")
def create_task():
    """Create a care task. Only a doctor may raise one; it starts 'pending'."""
    body = request.get_json(silent=True) or {}

    missing = [f for f in _CREATE_REQUIRED if not body.get(f)]
    if missing:
        return _json_error("missing required field(s): {}".format(", ".join(missing)), 400)

    # The clinical record must exist - a task cannot hang off nothing.
    try:
        parent = db.get_clinical_record(body["clinical_record_id"])
    except db.DatabaseClientError as exc:
        if exc.status_code == 404:
            return _json_error(
                "clinical record {} does not exist".format(body["clinical_record_id"]), 404
            )
        return _json_error("could not verify clinical record: {}".format(exc), 502)

    # Build the payload ourselves so the client cannot set status/completed_at.
    payload = {key: body[key] for key in _CREATE_FIELDS if body.get(key) is not None}
    payload["status"] = PENDING
    payload["completed_at"] = None

    try:
        created = db.create_care_task(payload)
    except db.DatabaseClientError as exc:
        return _json_error("could not create care task: {}".format(exc), 502)

    # Echo the parent patient/admission context for the caller's convenience.
    return (
        jsonify(
            {
                "care_task": created,
                "clinical_record_id": parent.get("record_id"),
                "patient_id": parent.get("patient_id"),
            }
        ),
        201,
    )


# ============================================================
# Plain edit - description / notes / due date. Not a status change.
# Doctor (fix a description) or nurse (add notes) while the task is open.
# ============================================================
@care_tasks_bp.put("/<int:task_id>")
@require_role(*_TASK_ROLES)
def edit_task(task_id):
    """Update free-text / scheduling fields on a task that is still open."""
    try:
        task = _fetch_task(task_id)
    except db.DatabaseClientError as exc:
        return _json_error("care task lookup failed: {}".format(exc), 502)
    if task is None:
        return _json_error("care task {} not found".format(task_id), 404)

    # No edits once the task is finished or cancelled.
    if task.get("status") not in OPEN_STATES:
        return _json_error(
            "care task {} is '{}' and can no longer be edited".format(
                task_id, task.get("status")
            ),
            409,
        )

    body = request.get_json(silent=True) or {}
    changes = {key: body[key] for key in _EDIT_FIELDS if key in body}
    if not changes:
        return _json_error(
            "nothing to update - allowed fields: {}".format(", ".join(_EDIT_FIELDS)), 400
        )

    try:
        updated = db.update_care_task(task_id, changes)
    except db.DatabaseClientError as exc:
        return _json_error("could not update care task: {}".format(exc), 502)
    return jsonify({"care_task": updated}), 200


# ============================================================
# Soft delete -> status 'cancelled'. Shared doctor/nurse privilege.
# The row is never removed; db.delete_care_task is intentionally not used.
# ============================================================
@care_tasks_bp.delete("/<int:task_id>")
@require_role(*_TASK_ROLES)
def cancel_task(task_id):
    """Cancel a task raised in error / no longer needed. Keeps the row."""
    try:
        task = _fetch_task(task_id)
    except db.DatabaseClientError as exc:
        return _json_error("care task lookup failed: {}".format(exc), 502)
    if task is None:
        return _json_error("care task {} not found".format(task_id), 404)

    status = task.get("status")
    if status == CANCELLED:
        return jsonify({"care_task": task, "note": "already cancelled"}), 200
    if status == COMPLETED:
        # A finished task is closed out already - cancelling it would rewrite history.
        return _json_error("care task {} is already completed".format(task_id), 409)

    try:
        cancelled = db.update_care_task(task_id, {"status": CANCELLED})
    except db.DatabaseClientError as exc:
        return _json_error("could not cancel care task: {}".format(exc), 502)
    return jsonify({"care_task": cancelled, "note": "cancelled (soft delete)"}), 200


# ============================================================
# Nurse workflow: acknowledge then complete.
# require_assignment(_assigned_nurse_id) does the "is this YOUR task?" check -
# it 404s an unknown task and 403s a nurse who isn't the assignee. We stack
# require_role("nurse") so neither a doctor nor a specialist can drive these.
# ============================================================
@care_tasks_bp.put("/<int:task_id>/acknowledge")
@require_role("nurse")
@require_assignment(_assigned_nurse_id)
def acknowledge_task(task_id):
    """Assigned nurse accepts a pending task: pending -> acknowledged."""
    try:
        task = _fetch_task(task_id)
    except db.DatabaseClientError as exc:
        return _json_error("care task lookup failed: {}".format(exc), 502)
    if task is None:  # race: deleted between the decorator's lookup and now
        return _json_error("care task {} not found".format(task_id), 404)

    status = task.get("status")
    if status == ACKNOWLEDGED:
        return jsonify({"care_task": task, "note": "already acknowledged"}), 200
    if status not in CAN_ACKNOWLEDGE_FROM:
        return _json_error("cannot acknowledge a '{}' task".format(status), 409)

    try:
        updated = db.update_care_task(task_id, {"status": ACKNOWLEDGED})
    except db.DatabaseClientError as exc:
        return _json_error("could not acknowledge care task: {}".format(exc), 502)
    return jsonify({"care_task": updated}), 200


@care_tasks_bp.put("/<int:task_id>/complete")
@require_role("nurse")
@require_assignment(_assigned_nurse_id)
def complete_task(task_id):
    """
    Assigned nurse finishes the task: -> completed, completed_at stamped now.
    Allowed from 'pending' or 'acknowledged'. No admission check - completing
    after discharge is fine and just closes the task administratively.
    Optional JSON body: {"notes": "..."} to record a closing note.
    """
    try:
        task = _fetch_task(task_id)
    except db.DatabaseClientError as exc:
        return _json_error("care task lookup failed: {}".format(exc), 502)
    if task is None:
        return _json_error("care task {} not found".format(task_id), 404)

    status = task.get("status")
    if status == COMPLETED:
        return jsonify({"care_task": task, "note": "already completed"}), 200
    if status not in CAN_COMPLETE_FROM:
        return _json_error("cannot complete a '{}' task".format(status), 409)

    changes = {"status": COMPLETED, "completed_at": _now_iso()}
    body = request.get_json(silent=True) or {}
    if body.get("notes"):
        changes["notes"] = body["notes"]

    try:
        updated = db.update_care_task(task_id, changes)
    except db.DatabaseClientError as exc:
        return _json_error("could not complete care task: {}".format(exc), 502)

    who = get_current_user() or {}
    return jsonify({"care_task": updated, "completed_by": who.get("name")}), 200


# ------------------------------------------------------------
# Auth failures from require_role / require_assignment -> JSON, right status.
# ------------------------------------------------------------
@care_tasks_bp.errorhandler(AuthError)
def _on_auth_error(err):
    return jsonify({"error": err.message}), err.status

# 
@care_tasks_bp.errorhandler(db.DatabaseClientError)
def _on_db_error(err):
    # require_assignment's lookup (_assigned_nurse_id -> _fetch_task) runs before
    # any handler's try/except, so a database-API fault there would otherwise be a
    # bare 500. Map it to the same 502 the in-handler except blocks return.
    return _json_error("care task lookup failed: {}".format(err), 502)
