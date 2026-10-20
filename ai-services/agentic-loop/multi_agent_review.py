#!/usr/bin/env python3
"""Release 2 Multi-Agent Workflow Review mode (``--mode multi-agent``).

Reviews the workflow history produced by the shared Multi-Agent Server:

1. PLAN     list the workflows to review and the checks to apply.
2. ACT      load each workflow, from a JSON file (``--workflow``) or from the
            Multi-Agent Server (``--workflow-id`` -> ``GET /workflows/<id>``).
3. OBSERVE  summarise the stages, agents, evidence and audit log unchanged.
4. CHECK    deterministic rules: Planner -> Worker -> Reviewer -> Human order,
            a plan with required evidence, a worker result that cites
            evidence, reviewer findings, a valid human decision and an
            ordered audit log that covers every actor.
5. ADAPT    the model reviews the workflows and the check results and gives
            implementation and review recommendations.

The verdict comes from CHECK, never from the model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from review_common import (  # noqa: E402
    ADVISORY,
    EvidenceUnavailable,
    GenerateFn,
    add_model_args,
    bounded,
    call_model,
    check,
    checks_table,
    default_logs_dir,
    exit_code,
    load_prompt,
    md_json,
    model_from_args,
    read_json,
    review_section,
    save_review,
    summarise_checks,
    _run_id,
)

DEFAULT_MULTI_AGENT_URL = "http://127.0.0.1:8200"
DEFAULT_TIMEOUT = 30.0
STAGES = ("planner", "worker", "reviewer", "human")
STAGE_ALIASES = {"human_review": "human", "human-review": "human", "humanreview": "human"}
HUMAN_DECISIONS = ("approved", "corrected", "partially_accepted", "rejected")
DECISIONS_NEEDING_COMMENT = ("corrected", "partially_accepted", "rejected")

PLAN_CHECKS = [
    "MA1 stages run Planner -> Worker -> Reviewer -> Human Review",
    "MA2 Planner defines the plan and the required evidence",
    "MA3 Worker returns a result and cites the evidence it used",
    "MA4 Worker covers every piece of evidence the Planner required (advisory)",
    "MA5 Reviewer gives a verdict, risks and recommendations",
    "MA6 Human Review records a valid decision, a reviewer and a reason when needed",
    "MA7 Audit log is ordered and covers every actor",
    "MA8 Workflow status matches the human decision (advisory)",
]


# --------------------------------------------------------------------------- #
# Evidence source
# --------------------------------------------------------------------------- #


class MultiAgentClient:
    """Read-only client for the Multi-Agent Server's workflow history."""

    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def workflow(self, workflow_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/workflows/{urllib.parse.quote(workflow_id, safe='')}"
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"unsupported Multi-Agent URL: {self.base_url}")
        request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ValueError(f"Multi-Agent Server returned HTTP {error.code} for {workflow_id}") from error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise EvidenceUnavailable(f"Multi-Agent Server unavailable at {self.base_url}: {error}") from error
        return unwrap(body)


def unwrap(body: Any) -> Dict[str, Any]:
    """Accept a bare workflow or the {"success", "data"} envelope used by the backends."""

    if isinstance(body, dict) and "steps" not in body and isinstance(body.get("data"), dict):
        body = body["data"]
    if not isinstance(body, dict):
        raise ValueError("workflow history is not a JSON object")
    return body


# --------------------------------------------------------------------------- #
# Observation
# --------------------------------------------------------------------------- #


def _stage(step: Dict[str, Any]) -> str:
    name = str(step.get("agent") or step.get("role") or "").strip().lower()
    return STAGE_ALIASES.get(name, name)


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else ([] if value in (None, "") else [value])


def observe(workflow: Dict[str, Any], origin: str) -> Dict[str, Any]:
    steps = [s for s in workflow.get("steps") or [] if isinstance(s, dict)]
    stages = [_stage(s) for s in steps]

    def last_output(stage: str) -> Dict[str, Any]:
        for step, name in zip(reversed(steps), reversed(stages), strict=True):
            if name == stage:
                output = step.get("output")
                return output if isinstance(output, dict) else {"text": output}
        return {}

    planner, worker = last_output("planner"), last_output("worker")
    reviewer, human = last_output("reviewer"), last_output("human")
    audit = [e for e in workflow.get("audit_log") or [] if isinstance(e, dict)]
    return {
        "origin": origin,
        "workflow_id": workflow.get("workflow_id") or workflow.get("id"),
        "feature": workflow.get("feature"),
        "request": workflow.get("request"),
        "status": workflow.get("status"),
        "stages": stages,
        "step_times": [(s.get("started_at"), s.get("finished_at")) for s in steps],
        "planner": {"plan": _list(planner.get("plan")),
                    "required_evidence": [str(e) for e in _list(planner.get("required_evidence"))]},
        "worker": {"result": worker.get("result") or worker.get("text"),
                   "evidence_used": [str(e) for e in _list(worker.get("evidence_used"))]},
        "reviewer": {"verdict": reviewer.get("verdict"), "risks": reviewer.get("risks"),
                     "recommendations": _list(reviewer.get("recommendations"))},
        "human": {"decision": str(human.get("decision") or "").strip().lower() or None,
                  "reviewer": human.get("reviewer"), "comment": human.get("comment")},
        "audit_log": audit,
    }


# --------------------------------------------------------------------------- #
# Deterministic checks
# --------------------------------------------------------------------------- #


def _order_ok(stages: Sequence[str]) -> tuple[bool, str]:
    missing = [s for s in STAGES if s not in stages]
    if missing:
        return False, f"missing stage(s): {', '.join(missing)}"
    unknown = sorted({s for s in stages if s not in STAGES})
    if unknown:
        return False, f"unknown agent(s): {', '.join(unknown)}"
    first = {s: stages.index(s) for s in STAGES}
    last = {s: len(stages) - 1 - stages[::-1].index(s) for s in STAGES}
    if not first["planner"] < first["worker"]:
        return False, "Worker ran before the Planner"
    if not last["worker"] < last["reviewer"]:
        return False, "the last Worker output was never reviewed"
    if not last["reviewer"] < first["human"] or stages[-1] != "human":
        return False, "Human Review is not the final stage after the Reviewer"
    return True, " -> ".join(stages)


def _times_ordered(values: Sequence[Optional[str]]) -> bool:
    present = [v for v in values if v]
    return len(present) == len(values) and all(a <= b for a, b in zip(present, present[1:], strict=False))


def check_workflow(obs: Dict[str, Any]) -> List[Dict[str, Any]]:
    subject = str(obs["workflow_id"] or obs["origin"])
    results = []

    ok, detail = _order_ok(obs["stages"])
    results.append(check("MA1", "Stage order", ok, detail, subject=subject))

    plan, required = obs["planner"]["plan"], obs["planner"]["required_evidence"]
    results.append(check("MA2", "Planner output", bool(plan) and bool(required),
                         f"{len(plan)} plan step(s), {len(required)} required evidence item(s)",
                         subject=subject))

    used = obs["worker"]["evidence_used"]
    results.append(check("MA3", "Worker output", bool(obs["worker"]["result"]) and bool(used),
                         f"result {'present' if obs['worker']['result'] else 'missing'}, "
                         f"{len(used)} evidence item(s) cited", subject=subject))

    used_lower = {u.lower() for u in used}
    uncovered = [r for r in required if r.lower() not in used_lower]
    results.append(check("MA4", "Evidence coverage", not uncovered,
                         "all required evidence cited" if not uncovered
                         else f"not cited by Worker: {', '.join(uncovered)}",
                         severity=ADVISORY, subject=subject))

    reviewer = obs["reviewer"]
    ok = bool(reviewer["verdict"]) and bool(reviewer["recommendations"]) and reviewer["risks"] is not None
    results.append(check("MA5", "Reviewer output", ok,
                         f"verdict={reviewer['verdict']!r}, risks "
                         f"{'recorded' if reviewer['risks'] is not None else 'missing'}, "
                         f"{len(reviewer['recommendations'])} recommendation(s)", subject=subject))

    human = obs["human"]
    problems = []
    if human["decision"] not in HUMAN_DECISIONS:
        problems.append(f"decision {human['decision']!r} not in {', '.join(HUMAN_DECISIONS)}")
    if not human["reviewer"]:
        problems.append("no reviewer name")
    if human["decision"] in DECISIONS_NEEDING_COMMENT and not human["comment"]:
        problems.append(f"'{human['decision']}' needs a comment")
    results.append(check("MA6", "Human Review decision", not problems,
                         "; ".join(problems) or f"{human['decision']} by {human['reviewer']}",
                         subject=subject))

    audit = obs["audit_log"]
    problems = []
    if not audit:
        problems.append("audit log is empty")
    else:
        incomplete = [i for i, e in enumerate(audit) if not all(e.get(k) for k in ("timestamp", "actor", "event"))]
        if incomplete:
            problems.append(f"entries missing timestamp/actor/event: {incomplete}")
        if not _times_ordered([str(e.get("timestamp") or "") for e in audit]):
            problems.append("timestamps are not in order")
        actors = {STAGE_ALIASES.get(str(e.get("actor", "")).lower(), str(e.get("actor", "")).lower())
                  for e in audit}
        missing = [s for s in STAGES if s not in actors]
        if missing:
            problems.append(f"no audit entry from: {', '.join(missing)}")
    results.append(check("MA7", "Audit log", not problems,
                         "; ".join(problems) or f"{len(audit)} ordered entries", subject=subject))

    expected = "rejected" if human["decision"] == "rejected" else "completed"
    results.append(check("MA8", "Workflow status", obs["status"] == expected,
                         f"status={obs['status']!r}, expected {expected!r}", severity=ADVISORY,
                         subject=subject))
    return results


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


class MultiAgentReview:
    def __init__(self, *, generate: GenerateFn, files: Sequence[Path] = (),
                 workflow_ids: Sequence[str] = (), client: Optional[MultiAgentClient] = None,
                 log=print):
        if not files and not workflow_ids:
            raise ValueError("give at least one --workflow file or --workflow-id")
        if workflow_ids and client is None:
            raise ValueError("--workflow-id needs a Multi-Agent Server client")
        self.generate, self.files, self.ids = generate, list(files), list(workflow_ids)
        self.client, self.log = client, log

    def run(self) -> Dict[str, Any]:
        sources = [str(p) for p in self.files] + [f"workflow:{i}" for i in self.ids]
        plan = {"evidence": sources, "checks": PLAN_CHECKS}
        self.log(f"PLAN    {len(sources)} workflow(s), {len(PLAN_CHECKS)} checks each")

        observations, checks = [], []
        for path in self.files:
            self.log(f"ACT     load {path}")
            observations.append(observe(unwrap(read_json(path)), str(path)))
        for workflow_id in self.ids:
            self.log(f"ACT     GET /workflows/{workflow_id}")
            observations.append(observe(self.client.workflow(workflow_id), f"workflow:{workflow_id}"))

        for obs in observations:
            results = check_workflow(obs)
            checks.extend(results)
            failed = [r["id"] for r in results if not r["passed"]]
            self.log(f"OBSERVE {obs['workflow_id']} stages={'>'.join(obs['stages'])} "
                     f"decision={obs['human']['decision']} -> "
                     + ("all checks passed" if not failed else f"not passed: {', '.join(failed)}"))

        summary = summarise_checks(checks)
        self.log(f"CHECK   {summary['passed']}/{summary['checks']} check(s) passed -> {summary['verdict'].upper()}")

        evidence = {
            "workflows": [{**{k: v for k, v in o.items() if k != "audit_log"},
                           "worker": {**o["worker"], "result": bounded(str(o["worker"]["result"] or ""), 1500)},
                           "audit_events": len(o["audit_log"])} for o in observations],
            "checks": checks,
            "summary": summary,
        }
        self.log("ADAPT   reviewing workflows")
        result, error = call_model(
            self.generate, f"{load_prompt('multi_agent_review.txt')}\n\nWorkflow evidence:\n"
            f"{json.dumps(evidence, indent=2, ensure_ascii=False)}")
        if error:
            self.log(f"ADAPT   unavailable: {error}")
        return {"plan": plan, "observations": observations, "checks": checks,
                "adapt": {"result": result, "error": error}, "summary": summary}


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def render_markdown(record: Dict[str, Any]) -> str:
    summary = record["summary"]
    workflows = ["| Workflow | Feature | Stages | Human decision | Audit entries |", "|---|---|---|---|---|"]
    for o in record["observations"]:
        workflows.append(f"| {o['workflow_id']} | {o['feature'] or '-'} | {' -> '.join(o['stages'])} | "
                         f"{o['human']['decision']} ({o['human']['reviewer'] or '-'}) | {len(o['audit_log'])} |")
    return "\n\n".join([
        "# Release 2 Agentic Loop Evidence: Multi-Agent Workflow Review",
        f"- Run: `{record['run_id']}`\n- Loop model: `{record['model']}`\n"
        f"- Verdict: **{summary['verdict'].upper()}** ({summary['passed']}/{summary['checks']} checks passed)"
        + (f"\n- Warnings: {', '.join(summary['warnings'])}" if summary["warnings"] else ""),
        "## Workflows reviewed\n\n" + "\n".join(workflows),
        "## CHECK\n\n" + checks_table(record["checks"]),
        "## ADAPT: review and recommendations\n\n" + review_section(record["adapt"]),
        "## PLAN\n\n" + md_json(record["plan"]),
        "## OBSERVE\n\n" + md_json(record["observations"]),
        "## ADAPT (raw)\n\n" + md_json(record["adapt"]),
    ]) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Release 2 Multi-Agent Workflow Review mode.")
    parser.add_argument("--workflow", action="append", type=Path, default=[],
                        help="Workflow history JSON exported by the Multi-Agent Server (repeatable)")
    parser.add_argument("--workflow-id", action="append", default=[],
                        help="Fetch GET /workflows/<id> from the Multi-Agent Server (repeatable)")
    parser.add_argument("--multi-agent-url",
                        default=os.environ.get("HOMS_MULTI_AGENT_URL", DEFAULT_MULTI_AGENT_URL))
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    add_model_args(parser)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run_id = _run_id()
    try:
        review = MultiAgentReview(
            generate=model_from_args(args), files=args.workflow, workflow_ids=args.workflow_id,
            client=MultiAgentClient(args.multi_agent_url, args.timeout) if args.workflow_id else None)
        result = review.run()
        logs_dir = default_logs_dir(args)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except EvidenceUnavailable as error:
        print(str(error), file=sys.stderr)
        return 3

    record = {"schema_version": 1, "release": "R2", "mode": "multi-agent", "run_id": run_id,
              "model": args.model, "ollama_url": args.ollama_url,
              "multi_agent_url": args.multi_agent_url if args.workflow_id else None, **result}
    paths = save_review(record, logs_dir, "multi-agent-review", render_markdown)
    print(f"EVIDENCE {paths['markdown']}")
    print(f"EVIDENCE {paths['json']}")
    return exit_code(result["summary"], [result["adapt"]["error"]])


if __name__ == "__main__":
    raise SystemExit(main())
