"""Shared data contracts for the Release 2 multi-agent workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


SCHEMA_VERSION = "1.0"


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

    def execution(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("output")
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
