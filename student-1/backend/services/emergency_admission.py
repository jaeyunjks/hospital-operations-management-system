# AI-assisted emergency admission assistance
# Creation date: 02/09/2026
#
# This feature is intentionally scoped to Student-1 responsibilities only:
#   - emergency identity matching
#   - provisional patient creation when the record is incomplete
#   - emergency admission registration
#   - emergency capacity allocation
#   - duplicate review and reconciliation
#
# Clinical notes, clinician assignment, and care-task planning belong to the
# Clinical Staff Management feature (Student-2) and are deliberately excluded
# from this service contract.

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _normalise_name(value: str | None) -> str:
    text = (value or "").strip()
    return " ".join(text.split())

def _identity_strength(identity: Dict[str, Any]) -> int:
    score = 0
    if identity.get("first_name"):
        score += 2
    if identity.get("last_name"):
        score += 2
    if identity.get("date_of_birth"):
        score += 2
    if identity.get("medicare_number"):
        score += 2
    if identity.get("patient_id"):
        score += 2
    return score

# Return likely patient matches for an emergency or provisional search.
def find_existing_patients(identity: Dict[str, Any], candidates: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    search_terms = {
        "first_name": _normalise_name(identity.get("first_name") or identity.get("p_first_name")),
        "last_name": _normalise_name(identity.get("last_name") or identity.get("p_last_name")),
        "dob": (identity.get("date_of_birth") or identity.get("p_date_of_birth")),
        "mrn": identity.get("medicare_number") or identity.get("medicare_number"),
    }

    matches: List[Dict[str, Any]] = []
    for patient in candidates:
        score = 0
        patient_id = patient.get("patient_id")
        patient_map = {
            "first_name": _normalise_name(patient.get("p_first_name") or patient.get("first_name")),
            "last_name": _normalise_name(patient.get("p_last_name") or patient.get("last_name")),
            "dob": patient.get("p_date_of_birth") or patient.get("date_of_birth"),
            "mrn": patient.get("medicare_number") or patient.get("medicare_no"),
        }

        if search_terms["first_name"] and patient_map["first_name"] and search_terms["first_name"].lower() == patient_map["first_name"].lower():
            score += 3
        if search_terms["last_name"] and patient_map["last_name"] and search_terms["last_name"].lower() == patient_map["last_name"].lower():
            score += 3
        if search_terms["dob"] and patient_map["dob"] and search_terms["dob"] == patient_map["dob"]:
            score += 4
        if search_terms["mrn"] and patient_map["mrn"] and str(search_terms["mrn"]) == str(patient_map["mrn"]):
            score += 5

        if score:
            matches.append({"patient_id": patient_id, "match_score": score, "patient": patient})

    matches.sort(key=lambda item: item["match_score"], reverse=True)
    return matches

# Create a provisional patient record when the identity is incomplete but urgent care is required.
def create_provisional_patient(identity: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {
        "first_name": _normalise_name(identity.get("first_name") or identity.get("p_first_name")) or "Unknown",
        "last_name": _normalise_name(identity.get("last_name") or identity.get("p_last_name")) or "Unknown",
        "date_of_birth": identity.get("date_of_birth") or identity.get("p_date_of_birth"),
        "medicare_number": identity.get("medicare_number"),
        "data_quality_flag": "provisional",
        "created_at": _now_iso(),
        "identity_strength": _identity_strength(identity),
        "requires_reconciliation": _identity_strength(identity) < 6,
    }
    return cleaned

# Create a traceable emergency-priority admission with explicit data-quality marking.
# Clinical note capture and workflow tasks are intentionally not part of this scope.
def create_emergency_admission(patient_id: int | None, *, arrival_time: str | None = None, priority: str = "Emergency", data_quality_flag: str = "provisional", identifiers: Dict[str, Any] | None = None) -> Dict[str, Any]:
    record = {
        "patient_id": patient_id,
        "arrival_time": arrival_time or _now_iso(),
        "priority": priority,
        "data_quality_flag": data_quality_flag,
        "identifiers": identifiers or {},
        "created_at": _now_iso(),
        "status": "active",
    }
    if record["data_quality_flag"] not in {"provisional", "partial", "confirmed"}:
        record["data_quality_flag"] = "provisional"
    return record

# Assign available emergency capacity for the admission.
def assign_emergency_capacity(admission_id: int | str, *, capacity_id: int | str, assigned_to: str, reason: str = "Emergency priority") -> Dict[str, Any]:
    return {
        "admission_id": admission_id,
        "capacity_id": capacity_id,
        "assigned_to": assigned_to,
        "reason": reason,
        "assigned_at": _now_iso(),
        "status": "allocated",
    }

# Record the later reconciliation step when reception confirms the identity or resolves duplicates.
def reconcile_identity(admission_id: int | str, *, patient_id: int | str, resolved_identity: Dict[str, Any], reviewed_by: str) -> Dict[str, Any]:
    return {
        "admission_id": admission_id,
        "patient_id": patient_id,
        "resolved_identity": resolved_identity,
        "reviewed_by": reviewed_by,
        "reconciled_at": _now_iso(),
        "data_quality_flag": "confirmed",
        "duplicate_review_required": False,
    }

# Rank likely duplicate records for manual review instead of auto-merging.
def rank_duplicate_candidates(candidate_records: Iterable[Dict[str, Any]], query_identity: Dict[str, Any]) -> List[Dict[str, Any]]:
    ranked: List[Dict[str, Any]] = []
    for record in candidate_records:
        score = 0
        if not isinstance(record, dict):
            continue
        name = _normalise_name((record.get("p_first_name") or record.get("first_name")) + " " + (record.get("p_last_name") or record.get("last_name")))
        q_name = _normalise_name((query_identity.get("first_name") or query_identity.get("p_first_name")) + " " + (query_identity.get("last_name") or query_identity.get("p_last_name")))
        if name and q_name and name.lower() == q_name.lower():
            score += 5
        if record.get("p_date_of_birth") and query_identity.get("date_of_birth") and record.get("p_date_of_birth") == query_identity.get("date_of_birth"):
            score += 4
        if record.get("medicare_number") and query_identity.get("medicare_number") and str(record.get("medicare_number")) == str(query_identity.get("medicare_number")):
            score += 6
        if score:
            ranked.append({"record": record, "duplicate_score": score})

    ranked.sort(key=lambda item: item["duplicate_score"], reverse=True)
    return ranked

# Create a short summary of emergency capacity and prioritisation concerns.
# This is a Student-1 concern only; it should not include clinical note text or
# staff task records handled by Student-2.
def summarise_capacity(capacity_records: Iterable[Dict[str, Any]], emergency_context: str | None = None) -> str:
    items = list(capacity_records)
    summary = "Emergency capacity overview: "
    if not items:
        summary += "no capacity currently allocated."
    else:
        summary += "; ".join(
            f"{item.get('capacity_id', 'unknown')} assigned to {item.get('assigned_to', 'unassigned')}"
            for item in items
        )
    if emergency_context:
        summary += f" Context: {str(emergency_context).strip()[:200]}"
    return summary


__all__ = [
    "_now_iso",
    "find_existing_patients",
    "create_provisional_patient",
    "create_emergency_admission",
    "assign_emergency_capacity",
    "reconcile_identity",
    "rank_duplicate_candidates",
    "summarise_capacity",
]