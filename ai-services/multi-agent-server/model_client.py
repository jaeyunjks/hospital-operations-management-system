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
from typing import Any, Callable, Dict, Mapping, Optional, Protocol

from models import AGENT_OUTPUT_SCHEMAS, GenerationResult
from semantic_validation import (
    authoritative_option_evaluations,
    evaluate_hard_constraint,
    option_hard_constraints,
    validate_semantics,
)


ROLE_NAMES = ("planner", "worker", "reviewer")
DEFAULT_ROLE_MODELS = {
    "planner": "llama3.1:8b",
    "worker": "llama3.2:3b",
    "reviewer": "deepseek-r1:8b",
}
# Retained for compatibility with Phase 2A imports and the legacy --model option.
DEFAULT_MODEL = DEFAULT_ROLE_MODELS["planner"]
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
        validation_type: Optional[str] = None,
        semantic_validation: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.attempts = attempts
        self.provider = provider
        self.model = model
        self.validation_type = validation_type
        self.semantic_validation = semantic_validation


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
) -> Dict[str, Any]:
    """Validate schema then deterministic cross-stage semantics without coercion."""

    schema = AGENT_OUTPUT_SCHEMAS.get(role)
    if schema is None:
        raise ModelError(f"unsupported agent role: {role}", validation_type="schema_invalid")
    error = _schema_error(output, schema)
    if error:
        raise ModelError(
            f"{role} {error}",
            validation_type="schema_invalid",
        )

    report = validate_semantics(role, output, context)
    if not report["valid"]:
        summary = "; ".join(
            f"{item['code']}: {item['reason']}" for item in report["issues"]
        )
        raise ModelError(
            f"{role} semantic validation failed: {summary}",
            validation_type="semantic_invalid",
            semantic_validation=report,
        )
    return report


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


class RoleModelClient:
    """Route each supported agent role to its configured model client."""

    def __init__(self, clients: Mapping[str, ModelClient]):
        missing = sorted(set(ROLE_NAMES) - set(clients))
        unsupported = sorted(set(clients) - set(ROLE_NAMES))
        if missing:
            raise ValueError(
                "missing model client configuration for role(s): " + ", ".join(missing)
            )
        if unsupported:
            raise ValueError(
                "unsupported model client role(s): " + ", ".join(unsupported)
            )
        self.clients = dict(clients)

    def generate(
        self, *, role: str, prompt: str, context: Dict[str, Any]
    ) -> GenerationResult:
        try:
            client = self.clients[role]
        except KeyError as error:
            raise ModelError(f"unsupported agent role: {role}") from error
        return client.generate(role=role, prompt=prompt, context=context)


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
        constraints = option_hard_constraints(evidence)
        authoritative = authoritative_option_evaluations(evidence)
        evaluations = []
        eligible = []
        for option in evidence.get("options", []):
            checks = [
                {
                    "constraint_id": rule.get("id"),
                    "passed": evaluate_hard_constraint(option, rule) is True,
                }
                for rule in constraints
            ]
            risks = list(option.get("risk_indicators", []))
            result = {
                "option_id": option.get("id"),
                "label": option.get("label", option.get("id")),
                "hard_constraint_checks": checks,
                "eligible": authoritative.get(option.get("id"), {}).get(
                    "eligible", False
                ),
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
        validated_refs = []
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
        evaluated_ids = {
            item.get("option_id") for item in worker.get("evaluated_options", [])
        }
        supplied_ids = {item.get("id") for item in evidence.get("options", [])}
        if evaluated_ids != supplied_ids:
            unsupported.append("The Worker did not evaluate every supplied option.")

        if selected_id is not None and selected is None:
            unsupported.append("The selected option does not exist in the supplied evidence.")
        elif selected is not None:
            for rule in option_hard_constraints(evidence):
                if evaluate_hard_constraint(selected, rule) is not True:
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
                validated_refs.append(
                    {
                        "source": "worker_output",
                        "path": "proposed_solution.projected_value",
                        "value": independently_projected,
                    }
                )
            if isinstance(target, (int, float)) and independently_projected < target:
                risks.append("The proposal does not fully reach the target state.")
            risks.extend(selected.get("risk_indicators", []))

        approval_required = any(
            item.get("type") == "approval" for item in evidence.get("constraints", [])
        )

        accepted = selected is not None and not unsupported
        worker_execution = context.get("worker_execution", {})
        worker_fallback = (
            isinstance(worker_execution, dict)
            and worker_execution.get("fallback_used") is True
        )
        reviewer_fallback = context.get("_fallback_invoked") is True
        return {
            "review_status": (
                "validated_with_risks"
                if accepted and not worker_fallback and not reviewer_fallback
                else "changes_required"
            ),
            "validated_findings": [],
            "validated_evidence_refs": validated_refs,
            "risks": list(dict.fromkeys(risks)),
            "unsupported_claims": unsupported,
            "recommended_option_id": selected_id if accepted else None,
            "recommendation": (
                "Review the validated Worker proposal and authoritative evidence before action."
                if worker_fallback or reviewer_fallback
                else (
                    "The proposal is evidence-consistent, but identified risks "
                    "require human review."
                )
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
            validation_type: Optional[str] = None
            semantic_validation: Optional[Dict[str, Any]] = None
            output: Optional[Dict[str, Any]] = None
            try:
                output, normalisation = parse_json_object(raw_text)
                semantic_validation = validate_agent_output(role, output, context)
            except ModelError as error:
                validation_error = str(error)
                validation_type = error.validation_type or (
                    "malformed_json" if output is None else "schema_invalid"
                )
                semantic_validation = error.semantic_validation

            attempts.append(
                {
                    "attempt": attempt_number,
                    "provider": self.provider,
                    "model": self.model,
                    "raw_response": raw_text[:MAX_CAPTURED_RESPONSE_CHARS],
                    "raw_response_truncated": len(raw_text) > MAX_CAPTURED_RESPONSE_CHARS,
                    "normalisation": normalisation,
                    "validation_error": validation_error,
                    "validation_type": validation_type,
                    "semantic_validation": semantic_validation,
                    "valid": validation_error is None,
                }
            )
            if validation_error is None and output is not None:
                return GenerationResult(
                    output=output,
                    provider=self.provider,
                    model=self.model,
                    attempts=tuple(attempts),
                    semantic_validation=semantic_validation,
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
            validation_type=attempts[-1].get("validation_type") if attempts else None,
            semantic_validation=(
                attempts[-1].get("semantic_validation") if attempts else None
            ),
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
            fallback_context = {**context, "_fallback_invoked": True}
            result = self.fallback.generate(
                role=role, prompt=prompt, context=fallback_context
            )
            return GenerationResult(
                output=result.output,
                provider=result.provider,
                model=result.model,
                fallback_used=True,
                fallback_reason=str(error),
                attempts=error.attempts,
                semantic_validation=result.semantic_validation,
            )
