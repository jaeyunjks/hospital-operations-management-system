"""Shared data contracts for the Release 2 multi-agent workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


SCHEMA_VERSION = "1.0"


AGENT_OUTPUT_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "planner": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "goal",
            "required_evidence",
            "constraints",
            "planned_steps",
            "expected_result",
        ],
        "properties": {
            "goal": {"type": "string", "minLength": 1},
            "required_evidence": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "planned_steps": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "expected_result": {"type": "string", "minLength": 1},
        },
    },
    "worker": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "status",
            "plan_steps_followed",
            "missing_planner_evidence",
            "gap",
            "evaluated_options",
            "proposed_solution",
            "limitations",
            "mutation_executed",
        ],
        "properties": {
            "status": {
                "type": "string",
                "enum": [
                    "proposal_ready",
                    "no_eligible_option",
                    "insufficient_evidence",
                ],
            },
            "plan_steps_followed": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "missing_planner_evidence": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "gap": {
                "type": "object",
                "additionalProperties": False,
                "required": ["metric", "current", "target", "deficit"],
                "properties": {
                    "metric": {"type": "string", "minLength": 1},
                    "current": {"type": ["number", "null"]},
                    "target": {"type": ["number", "null"]},
                    "deficit": {"type": ["number", "null"]},
                },
            },
            "evaluated_options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "option_id",
                        "label",
                        "hard_constraint_checks",
                        "eligible",
                        "risk_indicators",
                    ],
                    "properties": {
                        "option_id": {"type": "string", "minLength": 1},
                        "label": {"type": "string", "minLength": 1},
                        "hard_constraint_checks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["constraint_id", "passed"],
                                "properties": {
                                    "constraint_id": {
                                        "type": "string",
                                        "minLength": 1,
                                    },
                                    "passed": {"type": "boolean"},
                                },
                            },
                        },
                        "eligible": {"type": "boolean"},
                        "risk_indicators": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
            "proposed_solution": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "selected_option_id",
                    "selected_option",
                    "metric_delta",
                    "projected_value",
                    "reasoning",
                ],
                "properties": {
                    "selected_option_id": {"type": ["string", "null"]},
                    "selected_option": {"type": ["string", "null"]},
                    "metric_delta": {"type": "number"},
                    "projected_value": {"type": ["number", "null"]},
                    "reasoning": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
            "limitations": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "mutation_executed": {"type": "boolean", "const": False},
        },
    },
    "reviewer": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "review_status",
            "validated_findings",
            "validated_evidence_refs",
            "risks",
            "unsupported_claims",
            "recommended_option_id",
            "recommendation",
            "human_approval_required",
            "mutation_verified_absent",
        ],
        "properties": {
            "review_status": {
                "type": "string",
                "enum": ["validated_with_risks", "changes_required"],
            },
            "validated_findings": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "validated_evidence_refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "path", "value"],
                    "properties": {
                        "source": {
                            "type": "string",
                            "enum": ["evidence", "planner_output", "worker_output"],
                        },
                        "path": {"type": "string", "minLength": 1},
                        "value": {},
                    },
                },
            },
            "risks": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "unsupported_claims": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "recommended_option_id": {"type": ["string", "null"]},
            "recommendation": {"type": "string", "minLength": 1},
            "human_approval_required": {"type": "boolean"},
            "mutation_verified_absent": {"type": "boolean", "const": True},
        },
    },
}


def utc_now() -> str:
    """Return a millisecond-precision UTC timestamp for audit records."""

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class HumanDecision(str, Enum):
    APPROVE = "approve"
    CORRECT = "correct"
    REJECT = "reject"
    PARTIALLY_ACCEPT = "partially_accept"

    @classmethod
    def parse(cls, value: str) -> "HumanDecision":
        normalised = value.strip().lower().replace("-", " ").replace("_", " ")
        aliases = {
            "approve": cls.APPROVE,
            "a": cls.APPROVE,
            "correct": cls.CORRECT,
            "c": cls.CORRECT,
            "reject": cls.REJECT,
            "r": cls.REJECT,
            "partially accept": cls.PARTIALLY_ACCEPT,
            "partial": cls.PARTIALLY_ACCEPT,
            "p": cls.PARTIALLY_ACCEPT,
        }
        try:
            return aliases[normalised]
        except KeyError as error:
            raise ValueError(
                "decision must be Approve, Correct, Reject, or Partially Accept"
            ) from error


@dataclass(frozen=True)
class GenerationResult:
    """One agent's structured output plus its real execution provenance."""

    output: Dict[str, Any]
    provider: str
    model: Optional[str] = None
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    attempts: tuple[Dict[str, Any], ...] = ()
    semantic_validation: Optional[Dict[str, Any]] = None

    def execution(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("output")
        data["attempt_count"] = len(self.attempts)
        return data


@dataclass(frozen=True)
class HumanReview:
    decision: HumanDecision
    comments: str
    timestamp: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "decision": self.decision.value,
            "comments": self.comments,
            "timestamp": self.timestamp,
        }
