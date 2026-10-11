"""Sequential Planner -> Worker -> Reviewer -> Human Review coordinator."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from agents.planner import Planner
from agents.reviewer import Reviewer
from agents.worker import Worker
from audit import AuditStore
from models import HumanReview, SCHEMA_VERSION, utc_now


HumanReviewFn = Callable[[Dict[str, Any]], HumanReview]
StageEventFn = Callable[[str, Dict[str, Any]], None]


class InvalidWorkflowStateError(RuntimeError):
    """Raised when a lifecycle action is invalid for the persisted state."""


class MultiAgentOrchestrator:
    """Own the workflow order and preserve every hand-off in the audit record."""

    def __init__(
        self,
        *,
        planner: Planner,
        worker: Worker,
        reviewer: Reviewer,
        audit_store: AuditStore,
        on_stage: Optional[StageEventFn] = None,
    ):
        self.planner = planner
        self.worker = worker
        self.reviewer = reviewer
        self.audit_store = audit_store
        self.on_stage = on_stage or (lambda _stage, _payload: None)

    def _run_stage(
        self,
        *,
        name: str,
        agent: Any,
        agent_input: Dict[str, Any],
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        started_at = utc_now()
        try:
            result = agent.run(agent_input)
        except (RuntimeError, ValueError) as error:
            attempts = list(getattr(error, "attempts", ()))
            record[name] = {
                "started_at": started_at,
                "completed_at": utc_now(),
                "input": agent_input,
                "output": None,
                "execution": {
                    "provider": getattr(error, "provider", None),
                    "model": getattr(error, "model", None),
                    "fallback_used": False,
                    "fallback_reason": None,
                    "attempts": attempts,
                    "attempt_count": len(attempts),
                    "error": str(error),
                },
            }
            self.audit_store.update(record)
            self.on_stage(name, record[name])
            raise
        record[name] = {
            "started_at": started_at,
            "completed_at": utc_now(),
            "input": agent_input,
            "output": result.output,
            "execution": result.execution(),
        }
        self.audit_store.update(record)
        self.on_stage(name, record[name])
        return result.output

    def start(
        self, *, task: str, evidence: Dict[str, Any]
    ) -> tuple[Dict[str, Any], str]:
        """Run all model stages and persist the Human Review checkpoint."""

        if not task.strip():
            raise ValueError("task must not be blank")
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("evidence must be a non-empty JSON object")

        workflow_id = str(uuid4())
        record: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "workflow_id": workflow_id,
            "timestamp": utc_now(),
            "task": task.strip(),
            "input_evidence": evidence,
            "planner": None,
            "worker": None,
            "reviewer": None,
            "human_review": None,
            "final_status": "in_progress",
            "final_result": None,
        }
        audit_path = self.audit_store.create(record)
        self.on_stage("workflow_started", {"workflow_id": workflow_id})

        try:
            planner_input = {"task": record["task"], "evidence": evidence}
            plan = self._run_stage(
                name="planner",
                agent=self.planner,
                agent_input=planner_input,
                record=record,
            )

            worker_input = {
                "task": record["task"],
                "planner_output": plan,
                "evidence": evidence,
            }
            work = self._run_stage(
                name="worker", agent=self.worker, agent_input=worker_input, record=record
            )

            reviewer_input = {
                "task": record["task"],
                "planner_output": plan,
                "worker_output": work,
                "evidence": evidence,
            }
            review = self._run_stage(
                name="reviewer",
                agent=self.reviewer,
                agent_input=reviewer_input,
                record=record,
            )
        except (RuntimeError, ValueError) as error:
            record["final_status"] = "failed"
            record["final_result"] = {
                "status": "failed",
                "error": str(error),
                "mutation_executed": False,
            }
            record["completed_at"] = utc_now()
            self.audit_store.update(record)
            self.on_stage("workflow_failed", record["final_result"])
            error.workflow_id = workflow_id
            error.audit_path = str(audit_path)
            raise

        record["final_status"] = "awaiting_human_review"
        self.audit_store.update(record)
        self.on_stage("human_review_required", {"audit_path": str(audit_path)})
        return record, str(audit_path)

    def submit_human_review(
        self, *, workflow_id: str, human_review: HumanReview
    ) -> tuple[Dict[str, Any], str]:
        """Finalise one persisted workflow that is waiting for Human Review."""

        if (
            human_review.decision.value == "correct"
            and not human_review.comments.strip()
        ):
            raise ValueError("correction details are required for Correct")

        def finalise(record: Dict[str, Any]) -> Dict[str, Any]:
            if record.get("final_status") != "awaiting_human_review":
                raise InvalidWorkflowStateError(
                    "workflow is not awaiting Human Review"
                )
            if not all(
                isinstance(record.get(role), dict)
                and isinstance(record[role].get("output"), dict)
                for role in ("planner", "worker", "reviewer")
            ):
                raise InvalidWorkflowStateError(
                    "Planner, Worker, and Reviewer must complete before Human Review"
                )

            work = record["worker"]["output"]
            review = record["reviewer"]["output"]
            record["human_review"] = human_review.to_dict()
            record["final_status"] = {
                "approve": "approved",
                "correct": "corrected",
                "reject": "rejected",
                "partially_accept": "partially_accepted",
            }[human_review.decision.value]
            record["final_result"] = {
                "status": record["final_status"],
                "proposal": work.get("proposed_solution"),
                "review": review,
                "human_decision": human_review.decision.value,
                "human_comments_or_correction": human_review.comments,
                "mutation_executed": False,
            }
            record["completed_at"] = utc_now()
            return record

        record, audit_path = self.audit_store.update_existing(workflow_id, finalise)
        self.on_stage("workflow_completed", record["final_result"])
        return record, str(audit_path)

    def run(
        self,
        *,
        task: str,
        evidence: Dict[str, Any],
        request_human_review: HumanReviewFn,
    ) -> tuple[Dict[str, Any], str]:
        """CLI-compatible start, blocking Human Review, and finalisation wrapper."""

        record, _audit_path = self.start(task=task, evidence=evidence)
        human_review = request_human_review(record)
        return self.submit_human_review(
            workflow_id=record["workflow_id"], human_review=human_review
        )
