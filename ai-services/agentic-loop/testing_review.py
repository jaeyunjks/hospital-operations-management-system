#!/usr/bin/env python3
"""Release 2 Testing Report Review mode (``--mode testing``).

Reviews the two kinds of Release 2 test evidence together:

- the local pre-commit security scan output (``pre-commit run --all-files``),
  which must cover Ruff security rules, detect-secrets and pip-audit (or an
  equivalent of each);
- each student's post-commit CI/CD results from their ``student-x.yml``
  workflow: a JUnit XML report (``pytest --junitxml``) and, optionally, the
  run summary from ``gh run view <id> --json ...``.

1. PLAN     list the evidence files found and the checks to apply.
2. ACT      read every evidence file.
3. OBSERVE  parse hook results, test cases and workflow conclusions.
4. CHECK    deterministic rules (see PLAN_CHECKS).
5. ADAPT    the model reviews the results and recommends fixes per student.

The verdict comes from CHECK, never from the model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ElementTree  # noqa: S405 - local CI artefacts produced by pytest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from review_common import (  # noqa: E402
    ADVISORY,
    GenerateFn,
    add_model_args,
    bounded,
    call_model,
    check,
    checks_table,
    cell,
    default_logs_dir,
    exit_code,
    load_prompt,
    md_json,
    model_from_args,
    parse_assignment,
    read_json,
    review_section,
    save_review,
    student_from_text,
    students_list,
    summarise_checks,
    _run_id,
)

DEFAULT_EVIDENCE_DIR = Path("docs") / "test-evidence"
DEFAULT_STUDENTS = "1,2,3,4,5"
MIN_ENDPOINT_TESTS = 2

# Required scan -> hook names accepted for it (the brief allows equivalents).
REQUIRED_SCANS = {
    "ruff-security": re.compile(r"ruff|bandit", re.IGNORECASE),
    "detect-secrets": re.compile(r"detect[-_ ]?secrets|gitleaks|trufflehog", re.IGNORECASE),
    "pip-audit": re.compile(r"pip[-_ ]?audit|safety|osv[-_ ]?scanner", re.IGNORECASE),
}

HOOK_LINE = re.compile(
    r"^(?P<name>\S.*?)\.{3,}(?:\((?P<note>[^)]*)\))?(?P<status>Passed|Failed|Skipped)\s*$")
ANSI = re.compile(r"\x1b\[[0-9;]*m")

PLAN_CHECKS = [
    "T1 pre-commit report exists",
    "T2 each required scan ran (Ruff security, detect-secrets, pip-audit or equivalents)",
    "T3 every required scan passed",
    "T4 every expected student has a CI/CD test report",
    f"T5 each student has at least {MIN_ENDPOINT_TESTS} passing endpoint tests and no failures",
    "T6 each student's student-x.yml run concluded successfully (when run evidence is given)",
]


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #


def parse_precommit(text: str) -> Dict[str, Any]:
    """Parse ``pre-commit run`` output into hook results with their detail lines."""

    hooks: List[Dict[str, Any]] = []
    for raw in ANSI.sub("", text).splitlines():
        line = raw.rstrip()
        match = HOOK_LINE.match(line)
        if match:
            hooks.append({"name": match.group("name").strip(), "status": match.group("status").lower(),
                          "note": match.group("note"), "details": []})
        elif hooks and line.strip():
            hooks[-1]["details"].append(line.strip())
    for hook in hooks:
        hook["hook_id"] = next((d.split(":", 1)[1].strip() for d in hook["details"]
                                if d.startswith("- hook id:")), None)
        hook["details"] = hook["details"][:40]
    return {"hooks": hooks}


def match_scans(hooks: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    return {scan: [h for h in hooks if pattern.search(f"{h['name']} {h.get('hook_id') or ''}")]
            for scan, pattern in REQUIRED_SCANS.items()}


def parse_junit(path: Path) -> Dict[str, Any]:
    try:
        root = ElementTree.parse(path).getroot()  # noqa: S314 - trusted local CI artefact
    except (OSError, ElementTree.ParseError) as error:
        raise ValueError(f"cannot parse JUnit XML {path}: {error}") from error
    cases = []
    for case in root.iter("testcase"):
        outcome = "passed"
        message = None
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (node.get("message") or node.text or "").strip()[:500]
                break
        cases.append({"name": case.get("name"), "classname": case.get("classname"),
                      "time": case.get("time"), "outcome": outcome, "message": message})
    counts = {k: sum(1 for c in cases if c["outcome"] == k) for k in ("passed", "failed", "error", "skipped")}
    return {"file": str(path), "tests": len(cases), **counts, "cases": cases}


def parse_run(path: Path) -> Dict[str, Any]:
    """Parse ``gh run view <id> --json conclusion,status,workflowName,headSha,url,createdAt``."""

    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a workflow run object")
    return {"file": str(path), **{k: data.get(k) for k in
            ("workflowName", "conclusion", "status", "headSha", "headBranch", "event", "url", "createdAt")}}


# --------------------------------------------------------------------------- #
# Evidence discovery
# --------------------------------------------------------------------------- #


def discover(evidence_dir: Path) -> Tuple[Optional[Path], List[Tuple[str, Path]], List[Tuple[str, Path]]]:
    """Find evidence under ``docs/test-evidence``.

    Layout::

        pre-commit*.txt                 local security scan output
        student-N/*.xml                 JUnit XML from student-N.yml
        student-N/*run*.json            gh run view output for student-N.yml
    """

    if not evidence_dir.is_dir():
        return None, [], []
    precommit = sorted(evidence_dir.glob("pre-commit*.txt"))
    junit, runs = [], []
    for folder in sorted(p for p in evidence_dir.iterdir() if p.is_dir()):
        student = student_from_text(folder.name)
        if not student:
            continue
        junit += [(student, p) for p in sorted(folder.glob("*.xml"))]
        runs += [(student, p) for p in sorted(folder.glob("*run*.json"))]
    return (precommit[-1] if precommit else None), junit, runs


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def check_precommit(report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if report is None:
        return [check("T1", "Pre-commit report", False, "no pre-commit output supplied")]
    hooks = report["hooks"]
    results = [check("T1", "Pre-commit report", bool(hooks),
                     f"{len(hooks)} hook result(s) parsed" if hooks else "no hook result lines found")]
    for scan, matched in match_scans(hooks).items():
        ran = [h for h in matched if h["status"] != "skipped"]
        results.append(check("T2", "Scan executed", bool(ran),
                             ", ".join(f"{h['name']} ({h['status']})" for h in matched) or "hook not found",
                             subject=scan))
        if ran:
            failed = [h["name"] for h in ran if h["status"] == "failed"]
            results.append(check("T3", "Scan passed", not failed,
                                 f"failed: {', '.join(failed)}" if failed else "passed", subject=scan))
    other_failed = [h["name"] for h in hooks if h["status"] == "failed"
                    and not any(p.search(h["name"]) for p in REQUIRED_SCANS.values())]
    if other_failed:
        results.append(check("T3", "Other hooks", False, f"failed: {', '.join(other_failed)}",
                             severity=ADVISORY))
    return results


def check_student(student: str, reports: Sequence[Dict[str, Any]],
                  runs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results = [check("T4", "CI/CD test report", bool(reports),
                     ", ".join(r["file"] for r in reports) or "no JUnit report found", subject=student)]
    if reports:
        passed = sum(r["passed"] for r in reports)
        broken = sum(r["failed"] + r["error"] for r in reports)
        results.append(check("T5", "Endpoint tests", passed >= MIN_ENDPOINT_TESTS and broken == 0,
                             f"{passed} passed, {broken} failed/error, "
                             f"{sum(r['skipped'] for r in reports)} skipped", subject=student))
    if runs:
        expected = student.replace("-", "")
        problems = []
        for run in runs:
            name = str(run.get("workflowName") or "")
            if name.lower().replace("-", "") != expected:
                problems.append(f"{run['file']}: workflow {name!r} is not {student}.yml")
            if run.get("conclusion") != "success":
                problems.append(f"{run['file']}: conclusion {run.get('conclusion')!r}")
        results.append(check("T6", "student-x.yml run", not problems,
                             "; ".join(problems) or ", ".join(str(r.get("url") or r["file"]) for r in runs),
                             subject=student))
    else:
        results.append(check("T6", "student-x.yml run", False, "no workflow run evidence supplied",
                             severity=ADVISORY, subject=student))
    return results


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


class TestingReview:
    def __init__(self, *, generate: GenerateFn, students: Sequence[str],
                 precommit: Optional[Path], junit: Sequence[Tuple[Optional[str], Path]],
                 runs: Sequence[Tuple[Optional[str], Path]], log=print):
        for student, path in [*junit, *runs]:
            if student is None:
                raise ValueError(f"cannot tell which student {path} belongs to; use student-N=PATH")
        self.generate, self.students, self.precommit = generate, list(students), precommit
        self.junit, self.runs, self.log = list(junit), list(runs), log

    def run(self) -> Dict[str, Any]:
        plan = {
            "students": self.students,
            "evidence": {"pre_commit": str(self.precommit) if self.precommit else None,
                         "junit": [f"{s}={p}" for s, p in self.junit],
                         "workflow_runs": [f"{s}={p}" for s, p in self.runs]},
            "checks": PLAN_CHECKS,
        }
        self.log(f"PLAN    pre-commit={'yes' if self.precommit else 'missing'} "
                 f"junit={len(self.junit)} runs={len(self.runs)} students={','.join(self.students)}")

        precommit = None
        if self.precommit:
            self.log(f"ACT     read {self.precommit}")
            try:
                text = self.precommit.read_text(encoding="utf-8", errors="replace")
            except OSError as error:
                raise ValueError(f"cannot read {self.precommit}: {error}") from error
            precommit = {"file": str(self.precommit), **parse_precommit(text)}
        reports: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.students}
        runs: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.students}
        for student, path in self.junit:
            self.log(f"ACT     read {path}")
            reports.setdefault(student, []).append(parse_junit(path))
        for student, path in self.runs:
            self.log(f"ACT     read {path}")
            runs.setdefault(student, []).append(parse_run(path))

        if precommit:
            self.log("OBSERVE pre-commit " + ", ".join(f"{h['name']}={h['status']}" for h in precommit["hooks"]))
        for student in reports:
            r = reports[student]
            self.log(f"OBSERVE {student} tests={sum(x['tests'] for x in r)} passed={sum(x['passed'] for x in r)} "
                     f"runs={[x.get('conclusion') for x in runs.get(student, [])]}")

        checks = check_precommit(precommit)
        for student in sorted(reports):
            checks += check_student(student, reports[student], runs.get(student, []))
        summary = summarise_checks(checks)
        self.log(f"CHECK   {summary['passed']}/{summary['checks']} check(s) passed -> {summary['verdict'].upper()}")

        evidence = {
            "pre_commit": None if not precommit else [
                {"name": h["name"], "status": h["status"], "details": bounded("\n".join(h["details"]), 800)}
                for h in precommit["hooks"]],
            "students": {s: {"tests": [{"name": c["name"], "outcome": c["outcome"], "message": c["message"]}
                                       for r in reports[s] for c in r["cases"]][:30],
                             "runs": [{k: v for k, v in x.items() if k != "file"} for x in runs.get(s, [])]}
                         for s in sorted(reports)},
            "checks": checks,
            "summary": summary,
        }
        self.log("ADAPT   reviewing test evidence")
        result, error = call_model(
            self.generate, f"{load_prompt('testing_review.txt')}\n\nTest evidence:\n"
            f"{json.dumps(evidence, indent=2, ensure_ascii=False)}")
        if error:
            self.log(f"ADAPT   unavailable: {error}")
        return {"plan": plan, "pre_commit": precommit, "ci": {"junit": reports, "runs": runs},
                "checks": checks, "adapt": {"result": result, "error": error}, "summary": summary}


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def render_markdown(record: Dict[str, Any]) -> str:
    summary = record["summary"]
    scans = ["| Hook | Status |", "|---|---|"] + [
        f"| {cell(h['name'])} | {h['status']} |" for h in (record["pre_commit"] or {}).get("hooks", [])]
    ci = ["| Student | Tests | Passed | Failed/Error | Workflow run |", "|---|---|---|---|---|"]
    for student, reports in sorted(record["ci"]["junit"].items()):
        runs = record["ci"]["runs"].get(student, [])
        ci.append(f"| {student} | {sum(r['tests'] for r in reports)} | {sum(r['passed'] for r in reports)} | "
                  f"{sum(r['failed'] + r['error'] for r in reports)} | "
                  f"{cell(', '.join(str(r.get('conclusion')) for r in runs))} |")
    return "\n\n".join([
        "# Release 2 Agentic Loop Evidence: Testing Report Review",
        f"- Run: `{record['run_id']}`\n- Loop model: `{record['model']}`\n"
        f"- Verdict: **{summary['verdict'].upper()}** ({summary['passed']}/{summary['checks']} checks passed)"
        + (f"\n- Warnings: {', '.join(summary['warnings'])}" if summary["warnings"] else ""),
        "## Pre-commit security scans\n\n" + "\n".join(scans),
        "## Post-commit CI/CD endpoint tests\n\n" + "\n".join(ci),
        "## CHECK\n\n" + checks_table(record["checks"]),
        "## ADAPT: review and recommendations\n\n" + review_section(record["adapt"]),
        "## PLAN\n\n" + md_json(record["plan"]),
        "## OBSERVE\n\n" + md_json({"pre_commit": record["pre_commit"], "ci": record["ci"]}),
        "## ADAPT (raw)\n\n" + md_json(record["adapt"]),
    ]) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Release 2 Testing Report Review mode.")
    parser.add_argument("--precommit", type=Path, help="Saved output of 'pre-commit run --all-files'")
    parser.add_argument("--junit", action="append", default=[],
                        help="student-N=PATH to a JUnit XML report (repeatable)")
    parser.add_argument("--run", action="append", default=[], dest="runs",
                        help="student-N=PATH to 'gh run view --json' output (repeatable)")
    parser.add_argument("--evidence-dir", type=Path,
                        help=f"Discover evidence here when no files are given (default {DEFAULT_EVIDENCE_DIR})")
    parser.add_argument("--students", default=DEFAULT_STUDENTS,
                        help="Students that must have CI/CD evidence (default 1,2,3,4,5)")
    add_model_args(parser)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run_id = _run_id()
    try:
        precommit, junit, runs = args.precommit, [parse_assignment(v) for v in args.junit], \
            [parse_assignment(v) for v in args.runs]
        if not (precommit or junit or runs):
            evidence_dir = args.evidence_dir or args.repo_root.resolve() / DEFAULT_EVIDENCE_DIR
            precommit, junit, runs = discover(evidence_dir)
            if not (precommit or junit or runs):
                raise ValueError(f"no evidence given and none found in {evidence_dir}")
        result = TestingReview(generate=model_from_args(args), students=students_list(args.students),
                               precommit=precommit, junit=junit, runs=runs).run()
        logs_dir = default_logs_dir(args)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    record = {"schema_version": 1, "release": "R2", "mode": "testing", "run_id": run_id,
              "model": args.model, "ollama_url": args.ollama_url, **result}
    paths = save_review(record, logs_dir, "testing-review", render_markdown)
    print(f"EVIDENCE {paths['markdown']}")
    print(f"EVIDENCE {paths['json']}")
    return exit_code(result["summary"], [result["adapt"]["error"]])


if __name__ == "__main__":
    raise SystemExit(main())
