"""Evidence for the shared Multi-Agent Server from one admission's discharge state.

The shared server only understands a generic contract: an objective, options
with attributes, structured constraints and unknowns. This module projects the
open care tasks, pending consultations and scheduled surgeries of ONE admission
into that contract, so the agents can judge whether it looks ready for discharge.

Rules learned from testing the server, kept deliberately:

  * ONE option only (ready_to_discharge). While any item is open the Worker
    returns "no_eligible_option", which here means "not ready".
  * Each true/false attribute has exactly one hard "equals true" constraint
    whose id is the attribute name. An extra boolean fails validation.
  * The option label equals its id and risk_indicators stays empty, so there is
    no free text for a live model to paraphrase.
  * The Planner may only cite top-level keys, so the facts are top-level.
    The admission id also sits at current_state.admission.admission_id because
    the server's history search filters on that dotted path.
  * No advisory preferences or policy text is sent as a constraint.

Counts and status words only: no names, notes or other free text from the
records ever enter the evidence. The evidence is read-only; the assigned
doctor alone decides on a discharge.
"""

from services import admission_data

SCHEMA_VERSION = "1.0"
TASK_PREFIX = "Discharge readiness review: "
EVIDENCE_TYPE = "student2_discharge_readiness"

OPTION_ID = "ready_to_discharge"
SCENARIO = "Hospital discharge readiness review for one admission"

# Status words, per schema.sql. A scheduled surgery is judged on its status,
# never on its date.
OPEN_TASK_STATUSES = ("pending", "acknowledged")
PENDING_CONSULTATION_STATUSES = ("requested", "in_review")
SCHEDULED_SURGERY_STATUS = "scheduled"

# note_status when the admission's clinical records do not share one status.
MIXED_NOTE_STATUS = "mixed"

# (attribute / constraint id, description). Every attribute below is a boolean,
# and each gets exactly one hard "equals true" constraint with the same id.
CHECKS = (
    ("no_open_care_tasks",
     "Every care task for the admission must be completed or cancelled."),
    ("no_pending_consultations",
     "No consultation request for the admission may still be waiting."),
    ("no_scheduled_surgery",
     "No surgery for the admission may still be scheduled."),
)
CHECKS_TARGET = len(CHECKS)

UNKNOWNS = (
    "Clinical stability of the patient beyond these three checks.",
    "Whether the clinical record notes are complete; note_status is information only.",
    "Whether any task, consultation or surgery changes after this snapshot.",
)


class NoClinicalRecordsError(Exception):
    """The admission has no clinical records, so there is nothing to review."""

    def __init__(self, admission_id):
        super().__init__(
            "Admission {} has no clinical records, so discharge readiness "
            "cannot be reviewed.".format(admission_id)
        )
        self.admission_id = admission_id


def _count_with_status(rows, statuses):
    return sum(1 for row in rows if row.get("status") in statuses)


def _note_status(records):
    """The records' status word if they all share one, else MIXED_NOTE_STATUS."""
    statuses = {record.get("status") for record in records}
    return statuses.pop() if len(statuses) == 1 else MIXED_NOTE_STATUS


def build_discharge_evidence(admission_id):
    """
    Return (task_text, evidence) for one admission.

    Raises NoClinicalRecordsError if the admission has no clinical records,
    ValueError if admission_id is not an integer, and lets
    database_client.DatabaseClientError through unchanged.
    """
    try:
        wanted = int(admission_id)
    except (TypeError, ValueError):
        raise ValueError("admission_id must be an integer") from None

    records = admission_data.clinical_records_for_admission(wanted)
    if not records:
        raise NoClinicalRecordsError(wanted)

    open_care_tasks = _count_with_status(
        admission_data.care_tasks_for_admission(wanted), OPEN_TASK_STATUSES
    )
    pending_consultations = _count_with_status(
        admission_data.consultations_for_admission(wanted), PENDING_CONSULTATION_STATUSES
    )
    scheduled_surgeries = _count_with_status(
        admission_data.surgery_requests_for_admission(wanted), (SCHEDULED_SURGERY_STATUS,)
    )

    checks = {
        "no_open_care_tasks": open_care_tasks == 0,
        "no_pending_consultations": pending_consultations == 0,
        "no_scheduled_surgery": scheduled_surgeries == 0,
    }
    checks_met = sum(checks.values())

    evidence = {
        "schema_version": SCHEMA_VERSION,
        "evidence_type": EVIDENCE_TYPE,
        "scenario": SCENARIO,
        # Identity for the server's history search (dotted path).
        "current_state": {
            "admission": {"admission_id": wanted},
            "patient_id": records[0].get("patient_id"),
        },
        # Facts are top-level on purpose: the Planner may only cite top-level keys.
        "open_care_tasks": open_care_tasks,
        "pending_consultations": pending_consultations,
        "scheduled_surgeries": scheduled_surgeries,
        "note_status": _note_status(records),  # information only, not a rule
        "objective": {
            "metric": "readiness checks met",
            "current": checks_met,
            "target": CHECKS_TARGET,
            "unit": "check",
        },
        "options": [
            {
                "id": OPTION_ID,
                "label": OPTION_ID,
                "attributes": checks,
                "outcomes": {
                    "metric_delta": CHECKS_TARGET - checks_met,
                    "projected_value": CHECKS_TARGET,
                },
                "risk_indicators": [],
            }
        ],
        "constraints": [
            {
                "id": check_id,
                "scope": "option",
                "type": "hard",
                "field": check_id,
                "operator": "equals",
                "value": True,
                "description": description,
            }
            for check_id, description in CHECKS
        ]
        + [
            {
                "id": "human_approval",
                "scope": "workflow",
                "type": "approval",
                "description": "The assigned doctor decides; AI is advisory only.",
            }
        ],
        "unknowns": list(UNKNOWNS),
    }
    return "{}admission {}".format(TASK_PREFIX, wanted), evidence
