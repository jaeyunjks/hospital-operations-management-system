"""Model interface and local implementations used by all three agents.

The deterministic implementation is deliberately evidence-driven and generic:
it operates on an objective, options, constraints and unknowns. The Staff/Shift
scenario is only one sample using that contract.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Protocol

from models import AGENT_OUTPUT_SCHEMAS, GenerationResult


DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MAX_ATTEMPTS = 3
MAX_CAPTURED_RESPONSE_CHARS = 20_000

class ModelError(RuntimeError):
    """Raised when a model cannot produce a usable structured result."""

    def __init__(
        self,
        message: str,
        *,
        attempts: tuple[Dict[str, Any], ...] = (),
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ):
        super().__init__(message)
        self.attempts = attempts
        self.provider = provider
        self.model = model


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def _schema_error(value: Any, schema: Dict[str, Any], path: str = "output") -> Optional[str]:
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    if expected and not any(_matches_json_type(value, item) for item in expected_types):
        return f"{path} must be {' or '.join(expected_types)}"
    if "const" in schema and value != schema["const"]:
        return f"{path} must equal {json.dumps(schema['const'])}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} must be one of {json.dumps(schema['enum'])}"
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        return f"{path} must not be blank"
    if isinstance(value, dict):
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            return f"{path} is missing required field(s): {', '.join(missing)}"
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(value) - set(properties))
            if unexpected:
                return f"{path} has unexpected field(s): {', '.join(unexpected)}"
        for key, child_schema in properties.items():
            if key in value:
                error = _schema_error(value[key], child_schema, f"{path}.{key}")
                if error:
                    return error
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            error = _schema_error(item, schema["items"], f"{path}[{index}]")
            if error:
                return error
    return None


def validate_agent_output(
    role: str, output: Dict[str, Any], context: Optional[Dict[str, Any]] = None
) -> None:
    """Validate structure and Phase 2A safety invariants without coercion."""

    schema = AGENT_OUTPUT_SCHEMAS.get(role)
    if schema is None:
        raise ModelError(f"unsupported agent role: {role}")
    error = _schema_error(output, schema)
    if error:
        raise ModelError(f"{role} {error}")

    if not context:
        return
    evidence = context.get("evidence", {})
    if role == "worker":
        supplied_ids = {item.get("id") for item in evidence.get("options", [])}
        evaluated_ids = {item.get("option_id") for item in output["evaluated_options"]}
        if evaluated_ids != supplied_ids:
            raise ModelError("worker must evaluate every supplied option exactly once")
        selected_id = output["proposed_solution"]["selected_option_id"]
        if selected_id is not None and selected_id not in supplied_ids:
            raise ModelError("worker selected_option_id is not in supplied evidence")
        if output["status"] == "proposal_ready" and selected_id is None:
            raise ModelError("worker proposal_ready requires a selected option")
    if role == "reviewer":
        approval_required = any(
            item.get("type") == "approval"
            for item in evidence.get("constraints", [])
        )
        if approval_required and output["human_approval_required"] is not True:
            raise ModelError("reviewer must preserve the supplied human approval constraint")


# Backward-compatible test/import name from Phase 1.
_validate_output = validate_agent_output


_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.IGNORECASE | re.DOTALL)


def parse_json_object(raw: str) -> tuple[Dict[str, Any], str]:
    """Parse one object with conservative fence/prose normalisation."""

    if not isinstance(raw, str) or not raw.strip():
        raise ModelError("model response was empty")
    text = raw.strip()
    candidates: list[tuple[str, str]] = [(text, "none")]
    fenced = _JSON_FENCE.fullmatch(text)
    if fenced:
        candidates.append((fenced.group(1).strip(), "json_code_fence_removed"))
    first, last = text.find("{"), text.rfind("}")
    if first >= 0 and last > first and (first > 0 or last < len(text) - 1):
        prefix, suffix = text[:first].strip(), text[last + 1 :].strip()
        if len(prefix) <= 200 and len(suffix) <= 200:
            candidates.append((text[first : last + 1], "json_object_extracted"))

    last_error = "invalid JSON"
    seen = set()
    for candidate, normalisation in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as error:
            last_error = f"invalid JSON at line {error.lineno} column {error.colno}: {error.msg}"
            continue
        if not isinstance(parsed, dict):
            raise ModelError("model output must be one JSON object")
        return parsed, normalisation
    raise ModelError(last_error)


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


OllamaTransport = Callable[[Dict[str, Any], float], Dict[str, Any]]


class OllamaModelClient:
    """Schema-constrained Ollama client with bounded corrective retries."""

    provider = "live_ollama"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        timeout: float,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        transport: Optional[OllamaTransport] = None,
    ):
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.transport = transport or self._post_generate

    def _post_generate(self, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace").strip()
            raise ModelError(
                f"Ollama returned HTTP {error.code}: {detail or error.reason}",
                provider=self.provider,
                model=self.model,
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise ModelError(
                f"Ollama request failed: {error}",
                provider=self.provider,
                model=self.model,
            ) from error
        except (json.JSONDecodeError, TypeError) as error:
            raise ModelError(
                "Ollama returned an invalid response envelope",
                provider=self.provider,
                model=self.model,
            ) from error
        if not isinstance(envelope, dict):
            raise ModelError(
                "Ollama response envelope was not an object",
                provider=self.provider,
                model=self.model,
            )
        return envelope

    @staticmethod
    def _correction_prompt(
        *, base_prompt: str, raw_response: str, error: str, schema: Dict[str, Any]
    ) -> str:
        return (
            f"{base_prompt}\n\nCORRECTION REQUIRED. Your previous response was invalid.\n"
            f"Validation error: {error}\n"
            f"Required JSON Schema:\n{json.dumps(schema, indent=2)}\n"
            "Previous invalid response:\n"
            f"{raw_response[:MAX_CAPTURED_RESPONSE_CHARS]}\n\n"
            "Return one corrected JSON object only. Do not use Markdown fences or "
            "include explanatory prose. Do not change facts to bypass validation."
        )

    def generate(
        self, *, role: str, prompt: str, context: Dict[str, Any]
    ) -> GenerationResult:
        schema = AGENT_OUTPUT_SCHEMAS.get(role)
        if schema is None:
            raise ModelError(f"unsupported agent role: {role}")
        base_prompt = (
            f"{prompt}\n\nRequired JSON Schema:\n{json.dumps(schema, indent=2)}\n\n"
            f"Workflow context:\n{json.dumps(context, indent=2)}"
        )
        request_prompt = base_prompt
        attempts: list[Dict[str, Any]] = []

        for attempt_number in range(1, self.max_attempts + 1):
            payload = {
                "model": self.model,
                "prompt": request_prompt,
                "stream": False,
                "format": schema,
                "keep_alive": "10m",
                "options": {"temperature": 0, "num_predict": 1600},
            }
            try:
                envelope = self.transport(payload, self.timeout)
            except ModelError as error:
                raise ModelError(
                    str(error),
                    attempts=tuple(attempts),
                    provider=self.provider,
                    model=self.model,
                ) from error

            raw = envelope.get("response") if isinstance(envelope, dict) else None
            raw_text = raw if isinstance(raw, str) else ""
            normalisation = "none"
            validation_error: Optional[str] = None
            output: Optional[Dict[str, Any]] = None
            try:
                output, normalisation = parse_json_object(raw_text)
                validate_agent_output(role, output, context)
            except ModelError as error:
                validation_error = str(error)

            attempts.append(
                {
                    "attempt": attempt_number,
                    "provider": self.provider,
                    "model": self.model,
                    "raw_response": raw_text[:MAX_CAPTURED_RESPONSE_CHARS],
                    "raw_response_truncated": len(raw_text) > MAX_CAPTURED_RESPONSE_CHARS,
                    "normalisation": normalisation,
                    "validation_error": validation_error,
                    "valid": validation_error is None,
                }
            )
            if validation_error is None and output is not None:
                return GenerationResult(
                    output=output,
                    provider=self.provider,
                    model=self.model,
                    attempts=tuple(attempts),
                )
            if attempt_number < self.max_attempts:
                request_prompt = self._correction_prompt(
                    base_prompt=base_prompt,
                    raw_response=raw_text,
                    error=validation_error or "unknown validation error",
                    schema=schema,
                )

        last_error = attempts[-1]["validation_error"] if attempts else "no response"
        raise ModelError(
            f"{role} output remained invalid after {self.max_attempts} attempts: {last_error}",
            attempts=tuple(attempts),
            provider=self.provider,
            model=self.model,
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
                attempts=error.attempts,
            )
