"""Advisory RAG enrichment for Staffing Recovery workflow evidence.

Operational facts remain untouched and authoritative. This module only adds a
separately labelled policy context obtained through the existing Release 1 RAG
adapter, or an explicit degraded status when that context is unavailable.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

from services.rag_client import (
    RAGDisabledError,
    RAGInputError,
    RAGInvalidResponse,
    RAGTimeout,
    RAGUnavailable,
    StaffShiftRAGClient,
)


POLICY_QUERY = (
    "What documented Student 5 rules govern an understaffed shift: per-shift "
    "coverage, deterministic eligibility blockers, availability and approved "
    "unavailability, assignment authority, and the Staff Manager human-approval "
    "decision boundary?"
)

OPERATIONAL_FACT_FIELDS = (
    "current_state",
    "objective",
    "options",
    "constraints",
    "unknowns",
    "provenance",
)

POLICY_LIMITATIONS = [
    "Retrieved policy text is advisory guidance, not live operational state.",
    (
        "Student 5 backend eligibility, coverage, assignment, and availability "
        "rules remain authoritative."
    ),
    (
        "Missing fatigue, overtime, rest-period, consecutive-shift, staffing-ratio, "
        "patient-load, and acuity rules must remain unknown unless documented."
    ),
    "Policy retrieval cannot create, change, or approve a staff assignment.",
]


def _unavailable_policy_context(reason: str) -> Dict[str, Any]:
    return {
        "context_type": "rag_policy_guidance",
        "retrieval_status": "unavailable",
        "query": POLICY_QUERY,
        "answer": None,
        "confidence": "none",
        "citations": [],
        "reason": reason,
        "limitations": list(POLICY_LIMITATIONS),
    }


def _retrieved_policy_context(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "context_type": "rag_policy_guidance",
        "retrieval_status": result["status"],
        "query": POLICY_QUERY,
        "answer": result["answer"],
        "confidence": result["confidence"],
        "citations": copy.deepcopy(result["citations"]),
        "reason": result["reason"],
        "limitations": list(POLICY_LIMITATIONS),
    }


def enrich_recovery_evidence(
    operational_evidence: Dict[str, Any],
    rag_client: StaffShiftRAGClient,
) -> Dict[str, Any]:
    """Return a copy enriched with policy context, never changed operational facts."""

    evidence = copy.deepcopy(operational_evidence)
    try:
        result = rag_client.ask(POLICY_QUERY)
    except RAGDisabledError:
        policy_context = _unavailable_policy_context("rag_disabled")
    except RAGTimeout:
        policy_context = _unavailable_policy_context("rag_timeout")
    except RAGUnavailable:
        policy_context = _unavailable_policy_context("rag_unavailable")
    except RAGInvalidResponse:
        policy_context = _unavailable_policy_context("rag_invalid_response")
    except RAGInputError:
        policy_context = _unavailable_policy_context("rag_query_invalid")
    else:
        policy_context = _retrieved_policy_context(result)

    evidence["evidence_separation"] = {
        "operational_fact_fields": list(OPERATIONAL_FACT_FIELDS),
        "policy_context_field": "policy_context",
        "policy_is_advisory": True,
        "operational_rules_remain_authoritative": True,
    }
    evidence["policy_context"] = policy_context
    return evidence
