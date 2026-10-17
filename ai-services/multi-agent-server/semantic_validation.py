"""Deterministic cross-stage validation for shared multi-agent outputs.

The checks in this module understand only the generic evidence contract:
top-level evidence categories, options, structured hard constraints, an
objective, and option outcomes. Domain-specific eligibility remains upstream.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


SUPPORTED_HARD_CONSTRAINT_OPERATORS = {"equals"}
SAFE_REVIEW_RECOMMENDATIONS = {
    "Review the validated Worker proposal and authoritative evidence before action.",
    "The proposal is evidence-consistent, but identified risks require human review.",
    "Do not approve the proposal until the unsupported claims are corrected.",
}


def _issue(code: str, reason: str) -> Dict[str, str]:
    return {"code": code, "reason": reason}


def _same_value(actual: Any, expected: Any) -> bool:
    """Compare JSON values without treating booleans as numbers."""

    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def option_hard_constraints(evidence: Dict[str, Any]) -> list[Dict[str, Any]]:
    constraints = evidence.get("constraints")
    if not isinstance(constraints, list):
        return []
    return [
        item
        for item in constraints
        if isinstance(item, dict)
        and item.get("scope") == "option"
        and item.get("type") == "hard"
    ]


def evaluate_hard_constraint(
    option: Dict[str, Any], constraint: Dict[str, Any]
) -> Optional[bool]:
    """Return the authoritative result, or None for an unsupported operator."""

    operator = constraint.get("operator")
    if operator not in SUPPORTED_HARD_CONSTRAINT_OPERATORS:
        return None
    attributes = option.get("attributes")
    field = constraint.get("field")
    if not isinstance(attributes, dict) or field not in attributes:
        return False
    actual = attributes[field]
    return _same_value(actual, constraint.get("value"))


def authoritative_option_evaluations(
    evidence: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Evaluate supplied options using only explicit structured hard constraints."""

    options = evidence.get("options")
    if not isinstance(options, list):
        return {}
    constraints = option_hard_constraints(evidence)
    results: Dict[str, Dict[str, Any]] = {}
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("id"), str):
            continue
        checks = []
        supported = True
        for constraint in constraints:
            result = evaluate_hard_constraint(option, constraint)
            if result is None:
                supported = False
            checks.append(
                {
                    "constraint_id": constraint.get("id"),
                    "passed": result,
                }
            )
        results[option["id"]] = {
            "option": option,
            "checks": checks,
            "eligible": supported and all(check["passed"] is True for check in checks),
        }
    return results


def _required_safety_constraints(evidence: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    constraints = evidence.get("constraints")
    if not isinstance(constraints, list):
        return []
    return [
        item
        for item in constraints
        if isinstance(item, dict) and item.get("type") in {"approval", "safety"}
    ]


def _planner_issues(output: Dict[str, Any], context: Dict[str, Any]) -> list[Dict[str, str]]:
    evidence = context.get("evidence")
    if not isinstance(evidence, dict):
        return []
    issues: list[Dict[str, str]] = []
    required = output.get("required_evidence", [])
    seen = set()
    for key in required:
        if key in seen:
            issues.append(
                _issue(
                    "planner_duplicate_evidence_key",
                    f"required_evidence repeats top-level key {key!r}",
                )
            )
        seen.add(key)
        if "." in key:
            issues.append(
                _issue(
                    "planner_nested_path_not_allowed",
                    f"required_evidence must use a top-level key, not {key!r}",
                )
            )
        elif key not in evidence:
            issues.append(
                _issue(
                    "planner_unknown_evidence_key",
                    f"required_evidence key {key!r} is not supplied",
                )
            )

    planned_constraints = set(output.get("constraints", []))
    for constraint in _required_safety_constraints(evidence):
        constraint_id = constraint.get("id")
        description = constraint.get("description")
        if constraint_id not in planned_constraints and description not in planned_constraints:
            issues.append(
                _issue(
                    "human_approval_requirement_missing"
                    if constraint.get("type") == "approval"
                    else "mutation_safety_requirement_missing",
                    f"Planner did not preserve supplied {constraint.get('type')} constraint "
                    f"{constraint_id!r}",
                )
            )
    return issues


def _objective_expectations(evidence: Dict[str, Any]) -> Dict[str, Any]:
    objective = evidence.get("objective")
    if not isinstance(objective, dict):
        return {}
    current = objective.get("current")
    target = objective.get("target")
    numeric = (
        isinstance(current, (int, float))
        and not isinstance(current, bool)
        and isinstance(target, (int, float))
        and not isinstance(target, bool)
    )
    return {
        "metric": objective.get("metric"),
        "current": current,
        "target": target,
        "deficit": max(target - current, 0) if numeric else None,
    }


def _worker_issues(output: Dict[str, Any], context: Dict[str, Any]) -> list[Dict[str, str]]:
    evidence = context.get("evidence")
    if not isinstance(evidence, dict):
        return []
    issues: list[Dict[str, str]] = []
    options = evidence.get("options")
    option_contract_applies = isinstance(options, list)
    supplied_options = {
        item.get("id"): item
        for item in options or []
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    } if option_contract_applies else {}
    authoritative = authoritative_option_evaluations(evidence)
    constraints = option_hard_constraints(evidence)

    constraint_ids = [item.get("id") for item in constraints]
    if len(constraint_ids) != len(set(constraint_ids)) or any(
        not isinstance(item, str) or not item for item in constraint_ids
    ):
        issues.append(
            _issue(
                "hard_constraint_definition_invalid",
                "Applicable hard constraints must have unique non-empty string ids",
            )
        )
    for constraint in constraints:
        if constraint.get("operator") not in SUPPORTED_HARD_CONSTRAINT_OPERATORS:
            issues.append(
                _issue(
                    "hard_constraint_operator_unsupported",
                    f"Constraint {constraint.get('id')!r} uses unsupported operator "
                    f"{constraint.get('operator')!r}",
                )
            )

    evaluations = output.get("evaluated_options", [])
    by_id: Dict[str, list[Dict[str, Any]]] = {}
    for evaluation in evaluations:
        option_id = evaluation.get("option_id")
        by_id.setdefault(option_id, []).append(evaluation)
        if option_contract_applies and option_id not in supplied_options:
            issues.append(
                _issue("worker_unknown_option", f"Worker evaluated unknown option {option_id!r}")
            )
    for option_id, matches in by_id.items():
        if len(matches) > 1:
            issues.append(
                _issue(
                    "worker_duplicate_option",
                    f"Worker evaluated option {option_id!r} more than once",
                )
            )
    if option_contract_applies:
        for option_id in supplied_options:
            if option_id not in by_id:
                issues.append(
                    _issue(
                        "worker_missing_option",
                        f"Worker did not evaluate supplied option {option_id!r}",
                    )
                )

    for option_id, matches in by_id.items():
        if option_id not in authoritative or len(matches) != 1:
            continue
        evaluation = matches[0]
        supplied = supplied_options[option_id]
        expected = authoritative[option_id]
        expected_label = supplied.get("label", option_id)
        if evaluation.get("label") != expected_label:
            issues.append(
                _issue(
                    "worker_option_label_mismatch",
                    f"Worker label for {option_id!r} does not match supplied evidence",
                )
            )
        if evaluation.get("risk_indicators") != supplied.get("risk_indicators", []):
            issues.append(
                _issue(
                    "worker_risk_indicators_mismatch",
                    f"Worker risks for {option_id!r} do not match supplied advisory risks",
                )
            )

        checks = evaluation.get("hard_constraint_checks", [])
        check_map: Dict[str, list[Dict[str, Any]]] = {}
        for check in checks:
            check_map.setdefault(check.get("constraint_id"), []).append(check)
        if set(check_map) - set(constraint_ids):
            issues.append(
                _issue(
                    "worker_unknown_hard_constraint",
                    f"Worker reported an unknown hard constraint for {option_id!r}",
                )
            )
        if any(len(items) != 1 for items in check_map.values()):
            issues.append(
                _issue(
                    "worker_duplicate_hard_constraint",
                    f"Worker repeated a hard constraint for {option_id!r}",
                )
            )
        if set(check_map) != set(constraint_ids):
            issues.append(
                _issue(
                    "worker_missing_hard_constraint",
                    f"Worker hard-constraint checks are incomplete for {option_id!r}",
                )
            )
        for expected_check in expected["checks"]:
            constraint_id = expected_check["constraint_id"]
            actual = check_map.get(constraint_id, [])
            if len(actual) == 1 and actual[0].get("passed") is not expected_check["passed"]:
                issues.append(
                    _issue(
                        "worker_hard_constraint_mismatch",
                        f"Worker result for constraint {constraint_id!r} on option "
                        f"{option_id!r} contradicts supplied evidence",
                    )
                )
        if evaluation.get("eligible") is not expected["eligible"]:
            issues.append(
                _issue(
                    "worker_eligibility_mismatch",
                    f"Worker eligibility for {option_id!r} contradicts hard constraints",
                )
            )

    plan = context.get("planner_output")
    if isinstance(plan, dict):
        expected_missing = [
            key for key in plan.get("required_evidence", []) if key not in evidence
        ]
        if output.get("missing_planner_evidence") != expected_missing:
            issues.append(
                _issue(
                    "worker_missing_evidence_mismatch",
                    "Worker missing_planner_evidence does not match supplied top-level evidence",
                )
            )
        if output.get("plan_steps_followed") != plan.get("planned_steps", []):
            issues.append(
                _issue(
                    "worker_plan_steps_mismatch",
                    "Worker plan_steps_followed does not preserve the Planner steps",
                )
            )
    else:
        expected_missing = []

    objective = _objective_expectations(evidence)
    if objective:
        gap = output.get("gap", {})
        if any(not _same_value(gap.get(key), expected) for key, expected in objective.items()):
            issues.append(
                _issue(
                    "worker_gap_mismatch",
                    "Worker gap does not match the structured objective",
                )
            )

    proposal = output.get("proposed_solution", {})
    selected_id = proposal.get("selected_option_id")
    selected = supplied_options.get(selected_id)
    if selected_id is not None:
        if not option_contract_applies or selected is None:
            issues.append(
                _issue(
                    "worker_selected_option_unknown",
                    f"Worker selected option {selected_id!r} is not supplied",
                )
            )
        else:
            selected_eval = by_id.get(selected_id, [])
            if len(selected_eval) != 1 or selected_eval[0].get("eligible") is not True:
                issues.append(
                    _issue(
                        "worker_selected_option_not_eligible",
                        f"Worker selected option {selected_id!r} was not evaluated as eligible",
                    )
                )
            if not authoritative[selected_id]["eligible"]:
                issues.append(
                    _issue(
                        "worker_selected_option_ineligible",
                        f"Selected option {selected_id!r} fails supplied hard constraints",
                    )
                )
            if proposal.get("selected_option") != selected.get("label", selected_id):
                issues.append(
                    _issue(
                        "worker_selected_option_label_mismatch",
                        "Selected option label does not match supplied evidence",
                    )
                )
            outcomes = selected.get("outcomes")
            delta = outcomes.get("metric_delta") if isinstance(outcomes, dict) else None
            if isinstance(delta, (int, float)) and not isinstance(delta, bool):
                if not _same_value(proposal.get("metric_delta"), delta):
                    issues.append(
                        _issue(
                            "worker_projection_mismatch",
                            "Selected option metric_delta contradicts supplied outcomes",
                        )
                    )
                current = objective.get("current")
                if isinstance(current, (int, float)) and not isinstance(current, bool):
                    if not _same_value(proposal.get("projected_value"), current + delta):
                        issues.append(
                            _issue(
                                "worker_projection_mismatch",
                                "Worker projected_value does not match current plus metric_delta",
                            )
                        )
    any_eligible = any(result["eligible"] for result in authoritative.values())
    expected_status = (
        "insufficient_evidence"
        if expected_missing
        else "proposal_ready"
        if any_eligible
        else "no_eligible_option"
    )
    if output.get("status") != expected_status:
        issues.append(
            _issue(
                "worker_status_mismatch",
                f"Worker status must be {expected_status!r} for the supplied evidence",
            )
        )
    if expected_status != "proposal_ready" and selected_id is not None:
        issues.append(
            _issue(
                "worker_status_mismatch",
                "A non-proposal Worker status cannot include a selected option",
            )
        )
    if output.get("mutation_executed") is not False:
        issues.append(
            _issue("mutation_safety_violation", "Worker must not execute a mutation")
        )
    return issues


def _resolve_reference(context: Dict[str, Any], source: str, path: str) -> tuple[bool, Any]:
    roots = {
        "evidence": context.get("evidence"),
        "planner_output": context.get("planner_output"),
        "worker_output": context.get("worker_output"),
    }
    current = roots.get(source)
    if current is None or not path or path.startswith(".") or path.endswith("."):
        return False, None
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return False, None
    return True, current


def _reviewer_issues(output: Dict[str, Any], context: Dict[str, Any]) -> list[Dict[str, str]]:
    evidence = context.get("evidence")
    worker = context.get("worker_output")
    if not isinstance(evidence, dict) or not isinstance(worker, dict):
        return []
    issues: list[Dict[str, str]] = []
    options = evidence.get("options")
    supplied_ids = {
        item.get("id") for item in options or [] if isinstance(item, dict)
    } if isinstance(options, list) else set()
    worker_proposal = worker.get("proposed_solution", {})
    worker_selected = worker_proposal.get("selected_option_id")
    worker_evaluations = {
        item.get("option_id"): item
        for item in worker.get("evaluated_options", [])
        if isinstance(item, dict)
    }
    authoritative = authoritative_option_evaluations(evidence)
    recommended = output.get("recommended_option_id")
    if output.get("recommendation") not in SAFE_REVIEW_RECOMMENDATIONS:
        issues.append(
            _issue(
                "reviewer_unstructured_recommendation",
                "Reviewer recommendation must use a safe generic instruction; "
                "option identity belongs in recommended_option_id",
            )
        )
    if recommended is not None:
        if recommended not in supplied_ids:
            issues.append(
                _issue(
                    "reviewer_unknown_option",
                    f"Reviewer recommended unknown option {recommended!r}",
                )
            )
        if recommended != worker_selected:
            issues.append(
                _issue(
                    "reviewer_worker_contradiction",
                    "Reviewer recommendation does not match the validated Worker selection",
                )
            )
        if worker_evaluations.get(recommended, {}).get("eligible") is not True:
            issues.append(
                _issue(
                    "reviewer_worker_contradiction",
                    "Reviewer recommended an option not evaluated as eligible by the Worker",
                )
            )
        if recommended in authoritative and not authoritative[recommended]["eligible"]:
            issues.append(
                _issue(
                    "reviewer_worker_contradiction",
                    "Reviewer recommended an option that fails supplied hard constraints",
                )
            )
    elif worker_selected is not None and output.get("review_status") == "validated_with_risks":
        issues.append(
            _issue(
                "reviewer_worker_contradiction",
                "A validated proposal must identify the Worker's selected option",
            )
        )

    # Free-text claims cannot be safely fact-checked. Structured references are the
    # sole supported representation for fields explicitly claimed as validated.
    if output.get("validated_findings"):
        issues.append(
            _issue(
                "reviewer_unsupported_finding",
                "validated_findings must be empty; use validated_evidence_refs",
            )
        )
    seen_refs = set()
    for reference in output.get("validated_evidence_refs", []):
        key = (reference.get("source"), reference.get("path"))
        if key in seen_refs:
            issues.append(
                _issue(
                    "reviewer_duplicate_evidence_reference",
                    f"Reviewer repeated evidence reference {key!r}",
                )
            )
        seen_refs.add(key)
        resolved, value = _resolve_reference(context, key[0], key[1])
        if not resolved or not _same_value(value, reference.get("value")):
            issues.append(
                _issue(
                    "reviewer_unsupported_finding",
                    f"Reviewer evidence reference {key!r} is missing or contradicts source data",
                )
            )

    approval_required = any(
        isinstance(item, dict) and item.get("type") == "approval"
        for item in evidence.get("constraints", [])
    ) if isinstance(evidence.get("constraints"), list) else False
    if approval_required and output.get("human_approval_required") is not True:
        issues.append(
            _issue(
                "human_approval_requirement_missing",
                "Reviewer must preserve the supplied Human Review requirement",
            )
        )
    if output.get("mutation_verified_absent") is not True:
        issues.append(
            _issue(
                "mutation_safety_violation",
                "Reviewer must verify that no mutation occurred",
            )
        )
    worker_execution = context.get("worker_execution")
    if (
        isinstance(worker_execution, dict)
        and worker_execution.get("fallback_used") is True
        and output.get("review_status") != "changes_required"
    ):
        issues.append(
            _issue(
                "reviewer_fallback_not_conservative",
                "Reviewer must request changes when Worker used deterministic fallback",
            )
        )
    return issues


def validate_semantics(
    role: str, output: Dict[str, Any], context: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return a compact, audit-safe semantic validation report."""

    context = context or {}
    handlers = {
        "planner": _planner_issues,
        "worker": _worker_issues,
        "reviewer": _reviewer_issues,
    }
    issues = handlers.get(role, lambda _output, _context: [])(output, context)
    return {"stage": role, "valid": not issues, "issues": issues}
