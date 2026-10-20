#!/usr/bin/env python3
"""Release 2 Cloud Deployment Report Review mode (``--mode cloud``).

Reviews the Azure or AWS deployment and supports the final release decision:

1. PLAN     load the probe file: the deployed frontend/backend URLs to check,
            CRUD probes, and probes proving AI-Mode, MCP, RAG and the
            Multi-Agent Server are disabled by default.
2. ACT      send each probe to the deployed application. Write probes (CRUD)
            only run with ``--allow-writes``.
3. OBSERVE  record status code, latency and a bounded body excerpt.
4. CHECK    deterministic rules: core endpoints answer, CRUD works, every AI
            service is disabled, the cloud-deployment.yml run succeeded after
            validation, and endpoints use HTTPS (advisory).
5. ADAPT    the model reviews the evidence. It may ask to re-probe failed
            read-only probes once (for example a container app scaled to
            zero), then recommends a release decision.
6. HUMAN    a person records the final release decision: release, hold or
            rollback (``--decision`` or an interactive prompt).

The verdict comes from CHECK and the decision from the human, never from the model.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

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
    cell,
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
    _iso_utc,
    _run_id,
    _utc_now,
)

DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRY_WAIT = 20.0
KINDS = ("core", "crud", "ai_disabled")
READ_METHODS = ("GET", "HEAD")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
DECISIONS = ("release", "hold", "rollback")
AI_SERVICES = ("ai-mode", "mcp", "rag", "multi-agent")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
ENV_REF = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")

PLAN_CHECKS = [
    "C1 every core probe (frontend, backend/API, database health) passes",
    "C2 every CRUD probe passes (create, read, update, delete)",
    "C3 AI-Mode, MCP, RAG and the Multi-Agent Server are disabled by default",
    "C4 cloud-deployment.yml concluded successfully and validation ran before deploy",
    "C5 every probed endpoint uses HTTPS (advisory)",
]

Sender = Callable[[str, str, Optional[Dict[str, Any]], Dict[str, str], float], Dict[str, Any]]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def http_send(method: str, url: str, body: Optional[Dict[str, Any]], extra_headers: Dict[str, str],
              timeout: float) -> Dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        return {"reachable": False, "error": f"unsupported URL scheme: {url}"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json, text/html;q=0.9", **extra_headers}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - scheme checked
            status, text = response.status, response.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status, text = error.code, error.read(200_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"reachable": False, "error": str(error),
                "duration_ms": round((time.monotonic() - start) * 1000)}
    return {"reachable": True, "status": status, "body": text,
            "duration_ms": round((time.monotonic() - start) * 1000)}


# --------------------------------------------------------------------------- #
# Probe file
# --------------------------------------------------------------------------- #


def load_probes(path: Path) -> Dict[str, Any]:
    spec = read_json(path)
    if not isinstance(spec, dict) or not isinstance(spec.get("probes"), list) or not spec["probes"]:
        raise ValueError(f"{path} must be an object with a non-empty 'probes' list")
    base = {k: str(v).rstrip("/") for k, v in (spec.get("base_urls") or {}).items()}
    default_headers = spec.get("headers") or {}
    seen = set()
    probes = []
    for index, raw in enumerate(spec["probes"], 1):
        if not isinstance(raw, dict):
            raise ValueError(f"probe {index} is not an object")
        probe = {"id": str(raw.get("id") or f"P{index}"), "kind": raw.get("kind", "core"),
                 "method": str(raw.get("method", "GET")).upper(), "url": str(raw.get("url", "")),
                 "body": raw.get("body"), "expect_status": raw.get("expect_status", [200]),
                 "expect_json": raw.get("expect_json") or {}, "expect_contains": raw.get("expect_contains"),
                 "save": raw.get("save") or {}, "service": raw.get("service"),
                 "headers": {**default_headers, **(raw.get("headers") or {})},
                 "description": raw.get("description")}
        if probe["id"] in seen:
            raise ValueError(f"duplicate probe id {probe['id']}")
        seen.add(probe["id"])
        if probe["kind"] not in KINDS:
            raise ValueError(f"probe {probe['id']}: kind must be one of {', '.join(KINDS)}")
        if probe["method"] not in READ_METHODS + WRITE_METHODS:
            raise ValueError(f"probe {probe['id']}: unsupported method {probe['method']}")
        if probe["kind"] == "ai_disabled" and probe["service"] not in AI_SERVICES:
            raise ValueError(f"probe {probe['id']}: ai_disabled probes need service in {', '.join(AI_SERVICES)}")
        if isinstance(probe["expect_status"], int):
            probe["expect_status"] = [probe["expect_status"]]
        for name, prefix in base.items():
            probe["url"] = probe["url"].replace(f"${{{name}}}", prefix)
        probe["headers"] = {str(k): resolve_env(str(v), probe["id"]) for k, v in probe["headers"].items()}
        probes.append(probe)
    return {"platform": spec.get("platform"), "environment": spec.get("environment"),
            "base_urls": base, "probes": probes}


def resolve_env(value: str, probe_id: str) -> str:
    """Replace ${env:NAME} so tokens come from the environment, never from the probe file."""

    def lookup(match: "re.Match[str]") -> str:
        if match.group(1) not in os.environ:
            raise ValueError(f"probe {probe_id}: environment variable {match.group(1)} is not set")
        return os.environ[match.group(1)]

    return ENV_REF.sub(lookup, value)


def json_path(value: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return KeyError
    return value


def evaluate(probe: Dict[str, Any], response: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    parsed: Any = None
    if not response.get("reachable"):
        reasons.append(f"unreachable: {response.get('error')}")
    else:
        if response["status"] not in probe["expect_status"]:
            reasons.append(f"status {response['status']} not in {probe['expect_status']}")
        try:
            parsed = json.loads(response["body"]) if response["body"].strip() else None
        except json.JSONDecodeError:
            parsed = None
        for dotted, expected in probe["expect_json"].items():
            actual = json_path(parsed, dotted)
            if actual is KeyError:
                reasons.append(f"JSON field {dotted} missing")
            elif actual != expected:
                reasons.append(f"JSON {dotted}={actual!r}, expected {expected!r}")
        if probe["expect_contains"] and probe["expect_contains"] not in response["body"]:
            reasons.append(f"body does not contain {probe['expect_contains']!r}")
    return {"passed": not reasons, "reasons": reasons, "json": parsed}


# --------------------------------------------------------------------------- #
# Deployment workflow evidence
# --------------------------------------------------------------------------- #


def check_workflow_run(path: Optional[Path]) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    if path is None:
        return None, [check("C4", "cloud-deployment.yml run", False, "no --workflow-run evidence supplied")]
    run = read_json(path)
    if not isinstance(run, dict):
        raise ValueError(f"{path} is not a workflow run object")
    name = str(run.get("workflowName") or run.get("name") or "")
    jobs = [j for j in run.get("jobs") or [] if isinstance(j, dict)]
    problems = []
    if "cloud-deployment" not in name.lower().replace(" ", "-"):
        problems.append(f"workflow {name!r} is not cloud-deployment.yml")
    if run.get("conclusion") != "success":
        problems.append(f"conclusion {run.get('conclusion')!r}")
    results = [check("C4", "cloud-deployment.yml run", not problems,
                     "; ".join(problems) or str(run.get("url") or path))]
    if jobs:
        def first(pattern: str) -> Optional[Dict[str, Any]]:
            return next((j for j in jobs if re.search(pattern, str(j.get("name", "")), re.IGNORECASE)), None)

        validate, deploy = first(r"valid|test|build|lint|scan"), first(r"deploy")
        ok = bool(validate and deploy and validate.get("conclusion") == "success"
                  and str(validate.get("completedAt") or "") <= str(deploy.get("startedAt") or "~"))
        results.append(check("C4", "Validation before deploy", ok,
                             f"validation={validate and validate.get('name')!r} "
                             f"({validate and validate.get('conclusion')}), deploy={deploy and deploy.get('name')!r}",
                             severity=ADVISORY))
    summary = {k: run.get(k) for k in ("workflowName", "conclusion", "status", "headSha", "url", "createdAt")}
    summary["jobs"] = [{k: j.get(k) for k in ("name", "conclusion", "startedAt", "completedAt")} for j in jobs]
    return summary, results


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


class CloudReview:
    def __init__(self, *, generate: GenerateFn, probes: Dict[str, Any], workflow_run: Optional[Path] = None,
                 report: Optional[Path] = None, allow_writes: bool = False, send: Sender = http_send,
                 timeout: float = DEFAULT_TIMEOUT, retry_wait: float = DEFAULT_RETRY_WAIT,
                 sleep: Callable[[float], None] = time.sleep, log=print):
        self.generate, self.spec, self.workflow_run, self.report = generate, probes, workflow_run, report
        self.allow_writes, self.send, self.timeout = allow_writes, send, timeout
        self.retry_wait, self.sleep, self.log = retry_wait, sleep, log
        self.saved: Dict[str, Any] = {}

    @staticmethod
    def _public(probe: Dict[str, Any]) -> Dict[str, Any]:
        """Probe fields safe to store as evidence (header values may hold tokens)."""

        return {k: probe[k] for k in ("id", "kind", "method", "url", "service", "description")}

    def _probe(self, probe: Dict[str, Any], attempt: int) -> Dict[str, Any]:
        missing = [n for n in PLACEHOLDER.findall(probe["url"]) if n not in self.saved]
        if probe["method"] in WRITE_METHODS and not self.allow_writes:
            return {**self._public(probe), "attempt": attempt, "skipped": "write probe; rerun with --allow-writes",
                    "observation": None, "check": {"passed": False, "reasons": ["skipped (write probe)"]}}
        if missing:
            return {**self._public(probe), "attempt": attempt,
                    "skipped": f"needs {', '.join(missing)} from an earlier probe",
                    "observation": None, "check": {"passed": False, "reasons": [f"missing {', '.join(missing)}"]}}
        url = PLACEHOLDER.sub(lambda m: str(self.saved[m.group(1)]), probe["url"])
        body = json.loads(PLACEHOLDER.sub(lambda m: str(self.saved.get(m.group(1), m.group(0))),
                                          json.dumps(probe["body"]))) if probe["body"] is not None else None
        self.log(f"ACT     {probe['id']} [{probe['kind']}] {probe['method']} {url}")
        response = self.send(probe["method"], url, body, probe["headers"], self.timeout)
        result = evaluate(probe, response)
        for name, dotted in probe["save"].items():
            value = json_path(result["json"], dotted)
            if value is not KeyError:
                self.saved[name] = value
        observation = {"url": url, "reachable": response.get("reachable"), "status": response.get("status"),
                       "duration_ms": response.get("duration_ms"), "error": response.get("error"),
                       "body": bounded(response.get("body") or "", 1500)}
        self.log(f"OBSERVE {probe['id']} status={observation['status']} {observation['duration_ms']}ms -> "
                 + ("PASS" if result["passed"] else "FAIL (" + "; ".join(result["reasons"]) + ")"))
        return {**self._public(probe), "attempt": attempt, "observation": observation,
                "check": {"passed": result["passed"], "reasons": result["reasons"]}}

    def _checks(self, results: Dict[str, Dict[str, Any]], run_checks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def group(kind: str) -> List[Dict[str, Any]]:
            return [r for r in results.values() if r["kind"] == kind]

        def failed_ids(items: Sequence[Dict[str, Any]]) -> str:
            return ", ".join(f"{r['id']} ({'; '.join(r['check']['reasons'])})"
                             for r in items if not r["check"]["passed"])

        checks = []
        core = group("core")
        checks.append(check("C1", "Core endpoints", bool(core) and all(r["check"]["passed"] for r in core),
                            failed_ids(core) or (f"{len(core)} probe(s) passed" if core else "no core probes")))
        crud = group("crud")
        checks.append(check("C2", "CRUD functions", bool(crud) and all(r["check"]["passed"] for r in crud),
                            failed_ids(crud) or (f"{len(crud)} probe(s) passed" if crud else "no CRUD probes")))
        ai = group("ai_disabled")
        for service in AI_SERVICES:
            probes = [r for r in ai if r["service"] == service]
            checks.append(check("C3", "AI service disabled", bool(probes) and all(r["check"]["passed"] for r in probes),
                                failed_ids(probes) or (f"{len(probes)} probe(s) confirm disabled" if probes
                                                       else "no probe for this service"), subject=service))
        checks += run_checks
        plain = sorted({r["observation"]["url"].split("/")[2] for r in results.values()
                        if r.get("observation") and r["observation"]["url"].startswith("http://")})
        checks.append(check("C5", "HTTPS endpoints", not plain,
                            f"plain HTTP: {', '.join(plain)}" if plain else "all probed endpoints use HTTPS",
                            severity=ADVISORY))
        return checks

    def run(self) -> Dict[str, Any]:
        probes = self.spec["probes"]
        plan = {"platform": self.spec["platform"], "environment": self.spec["environment"],
                "base_urls": self.spec["base_urls"], "allow_writes": self.allow_writes,
                "probes": [{**{k: p[k] for k in ("id", "kind", "method", "url", "service")},
                            "header_names": sorted(p["headers"])} for p in probes],
                "workflow_run": str(self.workflow_run) if self.workflow_run else None,
                "deployment_report": str(self.report) if self.report else None, "checks": PLAN_CHECKS}
        self.log(f"PLAN    {self.spec['platform'] or 'cloud'} {self.spec['environment'] or ''}: "
                 f"{len(probes)} probe(s), writes {'allowed' if self.allow_writes else 'skipped'}")

        rounds = [{"round": 1, "results": [self._probe(p, 1) for p in probes]}]
        final = {r["id"]: r for r in rounds[0]["results"]}
        attempted = [r for r in final.values() if r.get("observation")]
        if attempted and not any(r["observation"]["reachable"] for r in attempted):
            raise EvidenceUnavailable("no deployed endpoint was reachable; check the base URLs and the deployment")

        run_summary, run_checks = check_workflow_run(self.workflow_run)
        report_text = None
        if self.report:
            try:
                report_text = bounded(self.report.read_text(encoding="utf-8", errors="replace"), 4000)
            except OSError as error:
                raise ValueError(f"cannot read {self.report}: {error}") from error

        adapts = []
        for number in (1, 2):
            checks = self._checks(final, run_checks)
            summary = summarise_checks(checks)
            self.log(f"CHECK   round {number}: {summary['passed']}/{summary['checks']} passed -> "
                     f"{summary['verdict'].upper()}")
            retry_allowed = number == 1 and any(
                not r["check"]["passed"] and r["method"] in READ_METHODS and r.get("observation")
                for r in final.values())
            evidence = {"platform": self.spec["platform"], "retry_allowed": retry_allowed,
                        "probes": [{k: r.get(k) for k in ("id", "kind", "method", "service", "attempt", "skipped")}
                                   | {"status": (r.get("observation") or {}).get("status"),
                                      "duration_ms": (r.get("observation") or {}).get("duration_ms"),
                                      "reasons": r["check"]["reasons"]} for r in final.values()],
                        "workflow_run": run_summary, "deployment_report": report_text,
                        "checks": checks, "summary": summary}
            self.log(f"ADAPT   round {number}")
            result, error = call_model(self.generate, f"{load_prompt('cloud_review.txt')}\n\n"
                                       f"Deployment evidence:\n{json.dumps(evidence, indent=2, ensure_ascii=False)}")
            if error:
                self.log(f"ADAPT   unavailable: {error}")
            adapts.append({"round": number, "result": result, "error": error})
            retry = [final[i] for i in (result or {}).get("retry") or []
                     if isinstance(i, str) and i in final and not final[i]["check"]["passed"]
                     and final[i]["method"] in READ_METHODS and final[i].get("observation")] if retry_allowed else []
            if not retry:
                break
            self.log(f"ADAPT   re-probing {', '.join(r['id'] for r in retry)} after {self.retry_wait:g}s")
            self.sleep(self.retry_wait)
            spec = {p["id"]: p for p in probes}
            results = [self._probe(spec[r["id"]], 2) for r in retry]
            rounds.append({"round": 2, "results": results})
            final.update({r["id"]: r for r in results})

        return {"plan": plan, "rounds": rounds, "final": list(final.values()), "workflow_run": run_summary,
                "deployment_report": report_text, "checks": checks, "adapt": adapts[-1], "adapt_rounds": adapts,
                "summary": summary}


# --------------------------------------------------------------------------- #
# Human release decision
# --------------------------------------------------------------------------- #


def human_decision(args: argparse.Namespace, summary: Dict[str, Any],
                   adapt: Dict[str, Any], ask: Callable[[str], str] = input) -> Dict[str, Any]:
    decision, reviewer, reason = args.decision, args.reviewer, args.reason
    if decision is None and sys.stdin.isatty():
        recommended = (adapt.get("result") or {}).get("recommended_decision")
        print(f"\nCHECK verdict: {summary['verdict'].upper()}  model recommends: {recommended or '-'}")
        while decision not in DECISIONS:
            decision = ask(f"Release decision ({'/'.join(DECISIONS)}): ").strip().lower()
        reviewer = reviewer or ask("Reviewer name: ").strip()
        reason = reason or ask("Reason: ").strip()
    if decision is None:
        return {"decision": "pending", "reviewer": None, "reason": None, "decided_at": None,
                "conflicts_with_checks": False}
    return {"decision": decision, "reviewer": reviewer or None, "reason": reason or None,
            "decided_at": _iso_utc(_utc_now()),
            "conflicts_with_checks": decision == "release" and summary["verdict"] != "pass"}


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def render_markdown(record: Dict[str, Any]) -> str:
    summary, human = record["summary"], record["human_decision"]
    rows = ["| Probe | Kind | Request | Status | Latency | Attempt | Result |", "|---|---|---|---|---|---|---|"]
    for r in record["final"]:
        obs = r.get("observation") or {}
        rows.append("| " + " | ".join(cell(v) for v in (
            r["id"], r["kind"] + (f" ({r['service']})" if r.get("service") else ""),
            f"{r['method']} {obs.get('url') or r.get('skipped')}", obs.get("status"),
            f"{obs['duration_ms']} ms" if obs.get("duration_ms") is not None else None, r["attempt"],
            "PASS" if r["check"]["passed"] else "FAIL: " + "; ".join(r["check"]["reasons"]))) + " |")
    run = record["workflow_run"] or {}
    recommended = (record["adapt"].get("result") or {}).get("recommended_decision")
    return "\n\n".join([
        "# Release 2 Agentic Loop Evidence: Cloud Deployment Review",
        f"- Run: `{record['run_id']}`\n- Platform: `{record['plan']['platform']}` "
        f"({record['plan']['environment'] or '-'})\n- Loop model: `{record['model']}`\n"
        f"- Verdict: **{summary['verdict'].upper()}** ({summary['passed']}/{summary['checks']} checks passed)"
        + (f"\n- Warnings: {', '.join(summary['warnings'])}" if summary["warnings"] else ""),
        "## Final human release decision\n\n"
        f"- Decision: **{human['decision'].upper()}**\n- Reviewer: {cell(human['reviewer'])}\n"
        f"- Reason: {cell(human['reason'])}\n- Decided at: {cell(human['decided_at'])}\n"
        f"- Model recommendation: {cell(recommended)}"
        + ("\n\n> The reviewer released despite failed checks." if human["conflicts_with_checks"] else ""),
        "## Deployed endpoint probes\n\n" + "\n".join(rows),
        "## cloud-deployment.yml run\n\n" + (
            f"- Workflow: {cell(run.get('workflowName'))}\n- Conclusion: {cell(run.get('conclusion'))}\n"
            f"- Commit: {cell(run.get('headSha'))}\n- URL: {cell(run.get('url'))}" if run else "No run evidence."),
        "## CHECK\n\n" + checks_table(record["checks"]),
        "## ADAPT: review and recommendations\n\n" + review_section(record["adapt"]),
        "## PLAN\n\n" + md_json(record["plan"]),
        "## OBSERVE (all rounds)\n\n" + md_json(record["rounds"]),
        "## ADAPT (raw)\n\n" + md_json(record["adapt_rounds"]),
    ]) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Release 2 Cloud Deployment Report Review mode.")
    parser.add_argument("--probes", type=Path, required=True,
                        help="Probe file (see examples/cloud-probes.example.json)")
    parser.add_argument("--workflow-run", type=Path,
                        help="Output of 'gh run view <id> --json "
                        "workflowName,conclusion,status,headSha,url,createdAt,jobs'")
    parser.add_argument("--report", type=Path, help="Cloud deployment report (Markdown/text) given to ADAPT")
    parser.add_argument("--allow-writes", action="store_true", help="Run CRUD write probes against the cloud database")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retry-wait", type=float, default=DEFAULT_RETRY_WAIT)
    parser.add_argument("--decision", choices=DECISIONS, help="Final human release decision (asked when omitted)")
    parser.add_argument("--reviewer", help="Name of the person making the release decision")
    parser.add_argument("--reason", help="Reason for the release decision")
    add_model_args(parser)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    run_id = _run_id()
    try:
        result = CloudReview(generate=model_from_args(args), probes=load_probes(args.probes),
                             workflow_run=args.workflow_run, report=args.report, allow_writes=args.allow_writes,
                             timeout=args.timeout, retry_wait=args.retry_wait).run()
        logs_dir = default_logs_dir(args)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except EvidenceUnavailable as error:
        print(str(error), file=sys.stderr)
        return 3

    decision = human_decision(args, result["summary"], result["adapt"])
    print(f"HUMAN   decision={decision['decision']} reviewer={decision['reviewer'] or '-'}")
    record = {"schema_version": 1, "release": "R2", "mode": "cloud", "run_id": run_id, "model": args.model,
              "ollama_url": args.ollama_url, **result, "human_decision": decision}
    paths = save_review(record, logs_dir, "cloud-deployment-review", render_markdown)
    print(f"EVIDENCE {paths['markdown']}")
    print(f"EVIDENCE {paths['json']}")
    return exit_code(result["summary"], [a["error"] for a in result["adapt_rounds"]])


if __name__ == "__main__":
    raise SystemExit(main())
