"""Model interface and local implementations used by all three agents.

The deterministic implementation is deliberately evidence-driven and generic:
it operates on an objective, options, constraints and unknowns. The Staff/Shift
scenario is only one sample using that contract.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Protocol

from models import GenerationResult


DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

_REQUIRED_OUTPUT_FIELDS = {
    "planner": {"goal", "required_evidence", "planned_steps", "expected_result"},
    "worker": {"status", "proposed_solution", "mutation_executed"},
    "reviewer": {
        "review_status",
        "validated_findings",
        "risks",
        "unsupported_claims",
        "recommendation",
        "human_approval_required",
    },
}


def _validate_output(role: str, output: Dict[str, Any]) -> None:
    """Enforce the stable cross-agent contract before a hand-off occurs."""

    missing = sorted(_REQUIRED_OUTPUT_FIELDS.get(role, set()) - set(output))
    if missing:
        raise ModelError(
            f"{role} output missing required field(s): {', '.join(missing)}"
        )
    expected_types = {
        "planner": {
            "goal": str,
            "required_evidence": list,
            "planned_steps": list,
            "expected_result": str,
        },
        "worker": {
            "status": str,
            "gap": dict,
            "evaluated_options": list,
            "proposed_solution": dict,
            "limitations": list,
            "mutation_executed": bool,
        },
        "reviewer": {
            "review_status": str,
            "validated_findings": list,
            "risks": list,
            "unsupported_claims": list,
            "recommendation": str,
            "human_approval_required": bool,
            "mutation_verified_absent": bool,
        },
    }
    for field, expected in expected_types.get(role, {}).items():
        if not isinstance(output.get(field), expected):
            raise ModelError(
                f"{role} output field '{field}' must be {expected.__name__}"
            )
    if role == "planner" and (
        not all(isinstance(value, str) for value in output["required_evidence"])
        or not all(isinstance(value, str) for value in output["planned_steps"])
    ):
        raise ModelError("planner evidence names and planned steps must be strings")
    if role == "worker" and output["mutation_executed"] is not False:
        raise ModelError("worker mutation_executed must be false in Phase 1")
    if role == "reviewer" and output["mutation_verified_absent"] is not True:
        raise ModelError("reviewer must verify that no mutation occurred in Phase 1")


class ModelError(RuntimeError):
    """Raised when a model cannot produce a usable structured result."""


class ModelClient(Protocol):
    def generate(
        self, *, role: str, prompt: str, context: Dict[str, Any]
    ) -> GenerationResult: ...


def _option_constraints(evidence: Dict[str, Any]) -> list[Dict[str, Any]]:
    return [
        item
        for item in evidence.get("constraints", [])
        if item.get("scope") == "option" and item.get("type") == "hard"
    ]


def _constraint_passes(option: Dict[str, Any], constraint: Dict[str, Any]) -> bool:
    actual = option.get("attributes", {}).get(constraint.get("field"))
    operator = constraint.get("operator", "equals")
    expected = constraint.get("value")
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    return False


class DeterministicModelClient:
    """No-network fallback for repeatable Phase 1 validation."""

    provider = "deterministic_fallback"

    def generate(
        self, *, role: str, prompt: str, context: Dict[str, Any]
    ) -> GenerationResult:
        del prompt  # Prompts remain the common contract; no model call is made here.
        handlers = {
            "planner": self._plan,
            "worker": self._work,
            "reviewer": self._review,
        }
        try:
            output = handlers[role](context)
        except KeyError as error:
            raise ModelError(f"unsupported agent role: {role}") from error
        return GenerationResult(output=output, provider=self.provider)

    @staticmethod
    def _plan(context: Dict[str, Any]) -> Dict[str, Any]:
        evidence = context["evidence"]
        required = [
            key
            for key in ("objective", "options", "constraints", "unknowns")
            if key in evidence
        ]
        return {
            "goal": context["task"],
            "required_evidence": required,
            "planned_steps": [
                "Measure the gap between the current and target state.",
                "Evaluate each supplied option against every hard constraint.",
                "Compare eligible options using supplied risks and policy context.",
                "Propose a reversible recommendation without executing a mutation.",
                "Send the proposal and evidence to an independent reviewer.",
            ],
            "constraints": [
                item.get("description", item.get("id", "unnamed constraint"))
                for item in evidence.get("constraints", [])
            ],
            "expected_result": (
                "A structured, evidence-backed proposal that remains pending until "
                "explicit human review."
            ),
        }

    @staticmethod
    def _work(context: Dict[str, Any]) -> Dict[str, Any]:
        evidence = context["evidence"]
        plan = context["planner_output"]
        missing_plan_evidence = [
            key for key in plan.get("required_evidence", []) if key not in evidence
        ]
        objective = evidence.get("objective", {})
        current = objective.get("current")
        target = objective.get("target")
        gap = target - current if isinstance(current, (int, float)) and isinstance(
            target, (int, float)
        ) else None
        constraints = _option_constraints(evidence)
        evaluations = []
        eligible = []
        for option in evidence.get("options", []):
            checks = [
                {
                    "constraint_id": rule.get("id"),
                    "passed": _constraint_passes(option, rule),
                }
                for rule in constraints
            ]
            risks = list(option.get("risk_indicators", []))
            result = {
                "option_id": option.get("id"),
                "label": option.get("label", option.get("id")),
                "hard_constraint_checks": checks,
                "eligible": all(check["passed"] for check in checks),
                "risk_indicators": risks,
            }
            evaluations.append(result)
            if result["eligible"]:
                eligible.append((len(risks), str(result["label"]), option, result))

        eligible.sort(key=lambda item: (item[0], item[1]))
        if missing_plan_evidence:
            eligible = []
        selected = eligible[0][2] if eligible else None
        selected_evaluation = eligible[0][3] if eligible else None
        delta = selected.get("outcomes", {}).get("metric_delta", 0) if selected else 0
        projected = current + delta if isinstance(current, (int, float)) else None
        metric = objective.get("metric", "objective metric")
        reasoning = []
        if selected_evaluation:
            reasoning.extend(
                f"Passed hard constraint: {check['constraint_id']}"
                for check in selected_evaluation["hard_constraint_checks"]
                if check["passed"]
            )
            if not selected_evaluation["risk_indicators"]:
                reasoning.append("No supplied option-specific risk indicator was identified.")

        return {
            "status": (
                "insufficient_evidence"
                if missing_plan_evidence
                else "proposal_ready" if selected else "no_eligible_option"
            ),
            "plan_steps_followed": list(plan.get("planned_steps", [])),
            "missing_planner_evidence": missing_plan_evidence,
            "gap": {
                "metric": metric,
                "current": current,
                "target": target,
                "deficit": max(gap, 0) if isinstance(gap, (int, float)) else None,
            },
            "evaluated_options": evaluations,
            "proposed_solution": {
                "selected_option_id": selected.get("id") if selected else None,
                "selected_option": selected.get("label") if selected else None,
                "metric_delta": delta,
                "projected_value": projected,
                "reasoning": reasoning,
            },
            "limitations": list(evidence.get("unknowns", [])),
            "mutation_executed": False,
        }

    @staticmethod
    def _review(context: Dict[str, Any]) -> Dict[str, Any]:
        evidence = context["evidence"]
        plan = context["planner_output"]
        worker = context["worker_output"]
        proposal = worker.get("proposed_solution", {})
        selected_id = proposal.get("selected_option_id")
        selected = next(
            (item for item in evidence.get("options", []) if item.get("id") == selected_id),
            None,
        )
        validated = []
        unsupported = []
        risks = list(evidence.get("unknowns", []))

        missing_plan_evidence = [
            key for key in plan.get("required_evidence", []) if key not in evidence
        ]
        if missing_plan_evidence:
            unsupported.append(
                "Planner-required evidence is missing: "
                + ", ".join(missing_plan_evidence)
                + "."
            )
        else:
            validated.append("All evidence categories required by the Planner are present.")
        evaluated_ids = {
            item.get("option_id") for item in worker.get("evaluated_options", [])
        }
        supplied_ids = {item.get("id") for item in evidence.get("options", [])}
        if evaluated_ids == supplied_ids:
            validated.append("The Worker evaluated every supplied option as planned.")
        else:
            unsupported.append("The Worker did not evaluate every supplied option.")

        if selected is None:
            unsupported.append("The selected option does not exist in the supplied evidence.")
        else:
            for rule in _option_constraints(evidence):
                if _constraint_passes(selected, rule):
                    validated.append(
                        f"{selected.get('label', selected_id)} satisfies {rule.get('id')}."
                    )
                else:
                    unsupported.append(
                        f"{selected.get('label', selected_id)} does not satisfy {rule.get('id')}."
                    )

            objective = evidence.get("objective", {})
            current = objective.get("current")
            target = objective.get("target")
            delta = selected.get("outcomes", {}).get("metric_delta", 0)
            independently_projected = (
                current + delta if isinstance(current, (int, float)) else None
            )
            if proposal.get("projected_value") != independently_projected:
                unsupported.append("The Worker's projected value does not match the evidence.")
            elif independently_projected is not None:
                validated.append(
                    f"Independent calculation projects {independently_projected}/{target} "
                    f"for {objective.get('metric', 'the objective metric')}."
                )
            if isinstance(target, (int, float)) and independently_projected < target:
                risks.append("The proposal does not fully reach the target state.")
            risks.extend(selected.get("risk_indicators", []))

        approval_required = any(
            item.get("type") == "approval" for item in evidence.get("constraints", [])
        )
        if approval_required:
            validated.append("The supplied constraints require human approval.")

        accepted = selected is not None and not unsupported
        return {
            "review_status": "validated_with_risks" if accepted else "changes_required",
            "validated_findings": validated,
            "risks": list(dict.fromkeys(risks)),
            "unsupported_claims": unsupported,
            "recommendation": (
                "The proposal is generally valid, but identified risks require human review."
                if accepted
                else "Do not approve the proposal until the unsupported claims are corrected."
            ),
            "human_approval_required": approval_required,
            "mutation_verified_absent": worker.get("mutation_executed") is False,
        }


class OllamaModelClient:
    """Small JSON-only Ollama client matching the existing R1 convention."""

    def __init__(self, *, model: str, base_url: str, timeout: float):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(
        self, *, role: str, prompt: str, context: Dict[str, Any]
    ) -> GenerationResult:
        payload = {
            "model": self.model,
            "prompt": f"{prompt}\n\nWorkflow context:\n{json.dumps(context, indent=2)}",
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
            output = json.loads(envelope.get("response", ""))
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ModelError(f"Ollama request failed: {error}") from error
        except (json.JSONDecodeError, TypeError) as error:
            raise ModelError("Ollama did not return a valid JSON object") from error
        if not isinstance(output, dict):
            raise ModelError("Ollama output was not a JSON object")
        _validate_output(role, output)
        return GenerationResult(
            output=output, provider="ollama", model=self.model
        )


class FallbackModelClient:
    """Try Ollama, then record why deterministic generation was used."""

    def __init__(self, primary: ModelClient, fallback: ModelClient):
        self.primary = primary
        self.fallback = fallback

    def generate(
        self, *, role: str, prompt: str, context: Dict[str, Any]
    ) -> GenerationResult:
        try:
            return self.primary.generate(role=role, prompt=prompt, context=context)
        except ModelError as error:
            result = self.fallback.generate(role=role, prompt=prompt, context=context)
            return GenerationResult(
                output=result.output,
                provider=result.provider,
                model=result.model,
                fallback_used=True,
                fallback_reason=str(error),
            )
