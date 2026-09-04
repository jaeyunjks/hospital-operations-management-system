"""
ai_summary.py - Flask Blueprint for the AI summary feature (ai_summaries table).

Mounted at /api/ai by app.py.

    POST /summarise-admission          generate + log a summary for ONE admission
    PUT  /summaries/<summary_id>/review record a human decision on a summary

DESIGN RULES (see schema.sql table 5 + the assessment brief):

  * A summary only ever covers a patient's CURRENT admission. Clinical history is
    pulled through the ONE admission-scoped endpoint
    (GET /api/clinical-records/admission/<id>) - we filter clinical_records by
    admission_id ourselves the same way that route does, so a summary can never
    mix the current stay with a previous one.

  * Role decides scope, and role-scoping fails LOUDLY:
      - doctor      -> summarise the admission's clinical records   (scope 'clinical')
      - specialist  -> summarise the consultation reason(s)          (scope 'consultation')
      - nurse       -> summarise ONLY their own assigned care tasks  (scope 'care_tasks')
        A nurse never sees the clinical record itself. A nurse asking for
        'clinical' or 'consultation' scope is rejected (403), not downgraded.

  * "AI suggests, human decides": every generation attempt is logged to
    ai_summaries, INCLUDING one where ollama_client returned its fallback
    "no summary" string. Nothing is silently skipped.

  * ai_summaries is append-only for the AI-generated content. There is NO delete
    endpoint here, and the review route may ONLY write two columns:
        review_status        -> 'accepted' | 'edited' | 'rejected'
                                (never back to 'pending')
        reviewed_by_staff_id
    summary_text / model_used / admission_id / patient_id / generated_at /
    summary_scope are frozen once written and are never touched again.

  * Access: a caller may only summarise an admission they are connected to
    (assigned doctor on a record, addressed specialist on a consultation, or
    assigned nurse on a care task). The review route requires the same
    connection (or being the original requester).

All database access goes through services.database_client - no raw SQL here.
"""

from flask import Blueprint, jsonify, request

from auth import AuthError, get_current_user, require_role
from services import database_client as db
from services import ollama_client

ai_summary_bp = Blueprint("ai_summary", __name__)

# ------------------------------------------------------------
# Scope constants (must match the CHECK constraint in schema.sql).
# ------------------------------------------------------------
SCOPE_CLINICAL = "clinical"
SCOPE_CONSULTATION = "consultation"
SCOPE_CARE_TASKS = "care_tasks"

# Which scope each role is allowed to request. A role/scope pair not listed
# here is refused outright - no silent downgrade.
_ROLE_SCOPES = {
    "doctor": {SCOPE_CLINICAL},
    "specialist": {SCOPE_CONSULTATION},
    "nurse": {SCOPE_CARE_TASKS},
}
# The scope we use when a role does not name one explicitly.
_DEFAULT_SCOPE_FOR_ROLE = {
    "doctor": SCOPE_CLINICAL,
    "specialist": SCOPE_CONSULTATION,
    "nurse": SCOPE_CARE_TASKS,
}

# Review decisions the PUT route accepts. 'pending' is deliberately absent -
# a review cannot un-review a summary.
_REVIEW_DECISIONS = ("accepted", "edited", "rejected")

# We have no dedicated "requested_by" column in ai_summaries, so the original
# requester's staff id is stamped into source_reference with this prefix at
# creation time. The review route reads it back to identify the requester.
_REQUESTED_BY_PREFIX = "requested_by_staff_id="


# ============================================================
# Small helpers
# ============================================================
def _error(message, status):
    """Uniform JSON error body."""
    return jsonify({"error": message}), status


def _current():
    """(role, user_id) for the acting user."""
    user = get_current_user() or {}
    return user.get("role"), user.get("id")


def _as_int(value):
    """Best-effort int coercion; None on failure (so id compares don't throw)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ============================================================
# Data gathering - all admission-scoped, all filtered locally so a previous
# admission's rows can never enter the text we send to the model.
# ============================================================
def _clinical_records_for_admission(admission_id):
    """
    Every clinical_records row for this admission and no other. Mirrors
    clinical_records._records_for_admission - the admission-scoped GET.
    """
    wanted = _as_int(admission_id)
    rows = db.list_clinical_records() or []
    return [r for r in rows if r.get("admission_id") == wanted]


def _consultations_for_admission(admission_id):
    """Every consultation_requests row for this admission and no other."""
    wanted = _as_int(admission_id)
    rows = db.list_consultation_requests() or []
    return [c for c in rows if c.get("admission_id") == wanted]


def _care_tasks_for_admission(admission_id):
    """
    care_tasks link to an admission only via clinical_record_id, so resolve the
    admission's record ids first, then the tasks hanging off them.
    """
    record_ids = {r.get("record_id") for r in _clinical_records_for_admission(admission_id)}
    tasks = db.list_care_tasks() or []
    return [t for t in tasks if t.get("clinical_record_id") in record_ids]


# ============================================================
# Access + text building, one function per scope. Each returns
#   (records_text, patient_id)  on success
# or raises AuthError if the caller has no connection to the admission.
# ============================================================
def _gather_clinical(admission_id, user_id):
    """Doctor: clinical records for the admission where they are the assigned doctor."""
    records = _clinical_records_for_admission(admission_id)
    mine = [r for r in records if r.get("doctor_id") == user_id]
    if not mine:
        raise AuthError(
            "You are not the assigned doctor on any record for this admission.",
            status=403,
        )

    # Flatten the clinical content into plain text for the model.
    parts = []
    for r in mine:
        parts.append(
            "Assessment: {}\nDiagnosis: {}\nCare plan: {}".format(
                r.get("assessment_notes") or "",
                r.get("diagnosis_summary") or "",
                r.get("care_plan") or "",
            )
        )
    return "\n\n".join(parts), mine[0].get("patient_id")


def _gather_consultation(admission_id, user_id):
    """Specialist: consultation reason(s) for the admission addressed to them."""
    consults = _consultations_for_admission(admission_id)
    mine = [c for c in consults if c.get("specialist_id") == user_id]
    if not mine:
        raise AuthError(
            "You hold no consultation request for this admission.",
            status=403,
        )

    parts = [c.get("reason_for_request") or "" for c in mine]
    return "\n\n".join(parts), mine[0].get("patient_id")


def _gather_care_tasks(admission_id, user_id):
    """
    Nurse: ONLY the care tasks on this admission that are assigned to this nurse.
    The nurse never sees the clinical record content itself.
    """
    tasks = _care_tasks_for_admission(admission_id)
    mine = [t for t in tasks if t.get("assigned_nurse_id") == user_id]
    if not mine:
        raise AuthError(
            "You have no assigned care task for this admission.",
            status=403,
        )

    parts = []
    for t in mine:
        parts.append(
            "Task: {} | Status: {} | Notes: {}".format(
                t.get("task_description") or "",
                t.get("status") or "",
                t.get("notes") or "",
            )
        )
    # patient_id isn't on care_tasks; take it from the admission's records.
    records = _clinical_records_for_admission(admission_id)
    patient_id = records[0].get("patient_id") if records else None
    return "\n".join(parts), patient_id


# Dispatch table: scope -> gather function.
_GATHER = {
    SCOPE_CLINICAL: _gather_clinical,
    SCOPE_CONSULTATION: _gather_consultation,
    SCOPE_CARE_TASKS: _gather_care_tasks,
}


# ============================================================
# POST /api/ai/summarise-admission
# Body: {"admission_id": <int>, "summary_scope": "<optional>"}
# ============================================================
@ai_summary_bp.post("/summarise-admission")
@require_role("doctor", "nurse", "specialist")
def summarise_admission():
    role, user_id = _current()
    body = request.get_json(silent=True) or {}

    admission_id = _as_int(body.get("admission_id"))
    if admission_id is None or admission_id <= 0:
        return _error("a positive integer 'admission_id' is required", 400)

    # --- Resolve the scope, failing loudly on a role/scope mismatch ---------
    requested_scope = body.get("summary_scope") or _DEFAULT_SCOPE_FOR_ROLE.get(role)
    allowed = _ROLE_SCOPES.get(role, set())
    if requested_scope not in allowed:
        # e.g. a nurse asking for 'clinical' / 'consultation'. Never downgrade.
        return _error(
            "role '{}' may not request summary_scope '{}' "
            "(allowed for this role: {})".format(
                role, requested_scope, ", ".join(sorted(allowed)) or "none"
            ),
            403,
        )

    # --- Gather the admission-scoped text this caller is entitled to -------
    try:
        gather = _GATHER[requested_scope]
        records_text, patient_id = gather(admission_id, user_id)
    except db.DatabaseClientError as exc:
        return _error("could not read admission data: {}".format(exc), 502)
    # AuthError (no connection to the admission) propagates to the error handler.

    # --- Ask the model. ollama_client never raises; on failure it returns
    #     its fallback string, which we STILL log. --------------------------
    summary_text = ollama_client.summarise_clinical_history(records_text, requested_scope)
    is_fallback = summary_text == ollama_client.FALLBACK_SUMMARY

    # --- Log the attempt to ai_summaries (append-only). -------------------
    payload = {
        "admission_id": admission_id,
        "patient_id": patient_id,
        "summary_text": summary_text,          # frozen after this write
        "model_used": ollama_client.OLLAMA_MODEL,
        "summary_scope": requested_scope,      # frozen after this write
        "review_status": "pending",            # always starts here
        # Stamp the requester so the review route can identify them later.
        "source_reference": "{}{}".format(_REQUESTED_BY_PREFIX, user_id),
    }
    try:
        created = db.create_ai_summary(payload)
    except db.DatabaseClientError as exc:
        return _error("could not log the AI summary: {}".format(exc), 502)

    # 200, not 201: the useful artifact is the summary text; the log row is a
    # side effect. `ai_unavailable` lets the frontend show a soft notice while
    # the rest of the workflow carries on unaffected.
    return (
        jsonify(
            {
                "summary_id": created.get("summary_id"),
                "summary": summary_text,
                "summary_scope": requested_scope,
                "review_status": "pending",
                "ai_unavailable": is_fallback,
            }
        ),
        200,
    )


# ============================================================
# PUT /api/ai/summaries/<summary_id>/review
# Body: {"review_status": "accepted|edited|rejected"}
# Records the reviewing staff member's decision. Only two columns move.
# ============================================================
@ai_summary_bp.put("/summaries/<int:summary_id>/review")
@require_role("doctor", "nurse", "specialist")
def review_summary(summary_id):
    role, user_id = _current()
    body = request.get_json(silent=True) or {}

    # --- Validate the decision -------------------------------------------
    decision = body.get("review_status")
    if decision == "pending":
        return _error("a review cannot set review_status back to 'pending'", 400)
    if decision not in _REVIEW_DECISIONS:
        return _error(
            "review_status must be one of: {}".format(", ".join(_REVIEW_DECISIONS)), 400
        )

    # --- Load the summary row -------------------------------------------
    try:
        summary = db.get_ai_summary(summary_id)
    except db.DatabaseClientError as exc:
        if exc.status_code == 404:
            return _error("no AI summary with id {}".format(summary_id), 404)
        return _error("could not read the AI summary: {}".format(exc), 502)

    # --- Authorisation: original requester OR an assignment relationship to
    #     the same admission. Fail loudly if neither holds. ----------------
    if not _may_review(summary, role, user_id):
        return _error(
            "you did not request this summary and have no assignment "
            "relationship to its admission",
            403,
        )

    # --- The ONLY two columns a review may write. Everything describing what
    #     the AI produced is left untouched. -----------------------------
    changes = {
        "review_status": decision,
        "reviewed_by_staff_id": user_id,
    }
    try:
        updated = db.update_ai_summary(summary_id, changes)
    except db.DatabaseClientError as exc:
        return _error("could not record the review: {}".format(exc), 502)

    return jsonify({"ai_summary": updated}), 200


def _may_review(summary, role, user_id):
    """
    True if `user_id` may review this summary:
      * they are the staff member who originally requested it, OR
      * they have an assignment relationship to the same admission
        (assigned doctor on a record, addressed specialist on a consultation,
         or assigned nurse on a care task).
    """
    admission_id = summary.get("admission_id")

    # 1. Original requester, read back from the source_reference stamp.
    ref = summary.get("source_reference") or ""
    if ref.startswith(_REQUESTED_BY_PREFIX):
        if _as_int(ref[len(_REQUESTED_BY_PREFIX):]) == user_id:
            return True

    # 2. Assignment relationship to the same admission, by role.
    try:
        if role == "doctor":
            records = _clinical_records_for_admission(admission_id)
            return any(r.get("doctor_id") == user_id for r in records)
        if role == "specialist":
            consults = _consultations_for_admission(admission_id)
            return any(c.get("specialist_id") == user_id for c in consults)
        if role == "nurse":
            tasks = _care_tasks_for_admission(admission_id)
            return any(t.get("assigned_nurse_id") == user_id for t in tasks)
    except db.DatabaseClientError:
        # Can't confirm a relationship -> treat as not permitted (fail safe).
        return False

    return False


# NOTE: there is deliberately no DELETE route for ai_summaries, and no route
# that can edit summary_text, model_used, admission_id, patient_id,
# generated_at, or summary_scope. That content is frozen once written.


# ------------------------------------------------------------
# Blueprint-local error handlers - uniform JSON, right status.
# ------------------------------------------------------------
@ai_summary_bp.errorhandler(AuthError)
def _on_auth_error(err):
    return jsonify({"error": err.message}), err.status


@ai_summary_bp.errorhandler(db.DatabaseClientError)
def _on_db_error(err):
    # The _gather_* helpers and _may_review touch the database API outside the
    # per-handler try/except; map a fault there to 502 rather than a bare 500.
    return _error("could not reach the database: {}".format(err), 502)
