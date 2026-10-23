"""
admission_data.py - admission-scoped reads shared by the AI summary and the
discharge review.

Every helper returns rows for ONE admission and never another: the database API
lists whole tables, so we filter locally on the exact integer admission_id.

care_tasks have no admission_id of their own - they link to an admission only
through their clinical record - so the admission's record ids are resolved
first, then the tasks hanging off them.

All database access goes through services.database_client - no raw SQL, no
direct HTTP. A fault there still raises database_client.DatabaseClientError;
nothing in this module catches it.
"""

from services import database_client as db


def _as_int(value):
    """Best-effort int coercion; None on failure (so id compares don't throw)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clinical_records_for_admission(admission_id):
    """
    Every clinical_records row for this admission and no other. Mirrors
    clinical_records._records_for_admission - the admission-scoped GET.
    """
    wanted = _as_int(admission_id)
    rows = db.list_clinical_records() or []
    return [r for r in rows if r.get("admission_id") == wanted]


def consultations_for_admission(admission_id):
    """Every consultation_requests row for this admission and no other."""
    wanted = _as_int(admission_id)
    rows = db.list_consultation_requests() or []
    return [c for c in rows if c.get("admission_id") == wanted]


def care_tasks_for_admission(admission_id):
    """
    care_tasks link to an admission only via clinical_record_id, so resolve the
    admission's record ids first, then the tasks hanging off them.
    """
    record_ids = {r.get("record_id") for r in clinical_records_for_admission(admission_id)}
    tasks = db.list_care_tasks() or []
    return [t for t in tasks if t.get("clinical_record_id") in record_ids]


def surgery_requests_for_admission(admission_id):
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


def patient_id_for_admission(admission_id):
    """
    patient_id isn't on care_tasks; take it from the admission's records.
    None if the admission has no clinical records.
    """
    records = clinical_records_for_admission(admission_id)
    return records[0].get("patient_id") if records else None


def assigned_doctor_ids(admission_id):
    """The set of doctor ids on this admission's clinical records."""
    return {
        r.get("doctor_id")
        for r in clinical_records_for_admission(admission_id)
        if r.get("doctor_id") is not None
    }
