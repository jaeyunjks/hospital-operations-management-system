#!/usr/bin/env python3
"""Release 1 RAG validation mode: Plan -> Act -> Observe -> Adapt over RAG.

The loop validates one feature's use of the shared HOMS RAG server:

1. PLAN     the model reads the feature's indexed document titles and sections
            and writes answerable questions plus one out-of-scope question.
            A fixed off-topic control question is always added.
2. ACT      each question is sent to the RAG server's ``POST /query``.
3. OBSERVE  status, confidence, citations and retrieval scores are recorded
            unchanged.
4. CHECK    deterministic rules decide pass/fail: answerable questions must be
            answered with citations from the feature's or shared knowledge;
            out-of-scope questions must return insufficient_context.
5. ADAPT    the model reviews the results and may rephrase failed answerable
            questions once; those retries form a second round.

The verdict comes from CHECK, never from the model. The model never edits
code or knowledge.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from agentic_loop import (  # noqa: E402
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_OLLAMA_URL,
    DEFAULT_REPO_ROOT,
    load_prompt,
    normalise_student,
    ollama_generate_json,
    _run_id,
)

DEFAULT_RAG_URL = "http://127.0.0.1:8100"
DEFAULT_RAG_TIMEOUT = 180.0
DEFAULT_QUESTIONS = 3
MAX_QUESTIONS = 6
MAX_ROUNDS = 2
MAX_GENERATED_QUESTION_LENGTH = 300
CONTROL_QUESTION = "What is the capital city of France?"

GenerateFn = Callable[[str], Dict[str, Any]]


class RagUnavailable(RuntimeError):
    """The shared RAG server could not be reached at all."""


# --------------------------------------------------------------------------- #
# RAG client
# --------------------------------------------------------------------------- #


class RagClient:
    """Blocking HTTP client for the shared RAG server's public API."""

    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"} if data
            else {"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read().decode("utf-8"))
            except (json.JSONDecodeError, OSError):
                body = {}
            detail = (body.get("error") or {}) if isinstance(body, dict) else {}
            return {"error": {"http_status": error.code, "code": detail.get("code", "http_error"),
                              "message": detail.get("message", f"HTTP {error.code}")}}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise RagUnavailable(f"RAG server unavailable at {self.base_url}: {error}") from error

    def sources(self, feature: str) -> Dict[str, Any]:
        return self._call("GET", f"/sources?feature={feature}")

    def query(self, question: str, feature: str) -> Dict[str, Any]:
        return self._call("POST", "/query", {"question": question, "feature": feature})


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


def _clean_question(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value if value and len(value) <= MAX_GENERATED_QUESTION_LENGTH else None


def fallback_questions(documents: Sequence[Dict[str, Any]], count: int) -> List[str]:
    """One question per document section, taken round-robin across documents."""

    queues = [[s for s in doc["sections"] if s != "Introduction"] or doc["sections"] for doc in documents]
    questions: List[str] = []
    while len(questions) < count and any(queues):
        for doc, sections in zip(documents, queues):
            if sections and len(questions) < count:
                questions.append(f"What does the {doc['title']} documentation say about {sections.pop(0).lower()}?")
    return questions


def build_cases(plan: Optional[Dict[str, Any]], documents: Sequence[Dict[str, Any]], count: int,
                user_questions: Sequence[str]) -> List[Dict[str, Any]]:
    """Turn the plan (or fallbacks) into numbered validation cases."""

    cases: List[Dict[str, Any]] = []

    def add(kind: str, question: str, origin: str) -> None:
        if all(case["question"].lower() != question.lower() for case in cases):
            cases.append({"id": f"Q{len(cases) + 1}", "kind": kind, "question": question, "origin": origin})

    if user_questions:
        for question in user_questions:
            add("answerable", question, "user")
    else:
        generated = [q for q in (_clean_question(v) for v in (plan or {}).get("answerable", []) or []) if q]
        for question in generated[:count]:
            add("answerable", question, "model")
        for question in fallback_questions(documents, count - len(generated[:count])):
            add("answerable", question, "fallback")
    out_of_scope = _clean_question((plan or {}).get("out_of_scope"))
    if out_of_scope:
        add("out_of_scope", out_of_scope, "model")
    add("out_of_scope", CONTROL_QUESTION, "control")
    return cases


def build_plan_prompt(component: str, documents: Sequence[Dict[str, Any]], count: int) -> str:
    facts = {
        "feature": component,
        "answerable_questions_wanted": count,
        "documents": [{"title": d["title"], "source": d["source"], "sections": d["sections"]} for d in documents],
    }
    return f"{load_prompt('rag_plan.txt')}\n\nKnowledge base:\n{json.dumps(facts, indent=2)}"


# --------------------------------------------------------------------------- #
# Observation and deterministic checks
# --------------------------------------------------------------------------- #


def observe(response: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the evidence fields of one /query response, unchanged."""

    if "error" in response:
        return {"error": response["error"]}
    return {
        "status": response.get("status"),
        "confidence": response.get("confidence"),
        "reason": response.get("reason"),
        "answer": response.get("answer"),
        "citations": [
            {key: citation.get(key) for key in ("id", "source", "section", "score")}
            for citation in response.get("citations") or []
        ],
        "retrieval": response.get("retrieval"),
        "duration_ms": response.get("duration_ms"),
    }


def check_case(kind: str, observation: Dict[str, Any], component: str) -> Dict[str, Any]:
    reasons: List[str] = []
    if "error" in observation:
        reasons.append(f"RAG server error: {observation['error'].get('code')}")
    elif kind == "answerable":
        if observation["status"] != "answered":
            reasons.append(f"expected answered, got {observation['status']} ({observation['reason']})")
        if not observation["citations"]:
            reasons.append("no citations")
        foreign = [c["source"] for c in observation["citations"]
                   if not str(c["source"]).startswith((f"{component}/", "shared/"))]
        if foreign:
            reasons.append(f"cited another feature's knowledge: {', '.join(foreign)}")
        if observation["status"] == "answered" and observation["confidence"] not in ("high", "medium", "low"):
            reasons.append(f"invalid confidence {observation['confidence']!r}")
    else:
        if observation["status"] != "insufficient_context":
            reasons.append(f"expected insufficient_context, got {observation['status']}")
        if observation["citations"]:
            reasons.append("refusal carried citations")
    return {"passed": not reasons, "reasons": reasons}


# --------------------------------------------------------------------------- #
# Loop
# --------------------------------------------------------------------------- #


class RagValidationLoop:
    def __init__(self, *, rag: Any, generate: GenerateFn, component: str,
                 questions: int = DEFAULT_QUESTIONS, user_questions: Sequence[str] = (),
                 log: Callable[[str], None] = print):
        if not 1 <= questions <= MAX_QUESTIONS:
            raise ValueError(f"questions must be between 1 and {MAX_QUESTIONS}")
        self.rag = rag
        self.generate = generate
        self.component = component
        self.questions = questions
        self.user_questions = [q for q in (_clean_question(v) for v in user_questions) if q]
        self.log = log

    def _model(self, prompt: str) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            return self.generate(prompt), None
        except RuntimeError as error:
            return None, str(error)

    def _act(self, cases: Sequence[Dict[str, Any]], round_number: int) -> List[Dict[str, Any]]:
        results = []
        for case in cases:
            self.log(f"ACT     round {round_number} {case['id']} [{case['kind']}] {case['question']}")
            observation = observe(self.rag.query(case["question"], self.component))
            check = check_case(case["kind"], observation, self.component)
            status = observation.get("status") or observation.get("error", {}).get("code")
            self.log(f"OBSERVE {case['id']} status={status} confidence={observation.get('confidence')} "
                     f"citations={len(observation.get('citations', []))} -> {'PASS' if check['passed'] else 'FAIL'}"
                     + (f" ({'; '.join(check['reasons'])})" if check["reasons"] else ""))
            results.append({**case, "observation": observation, "check": check})
        return results

    def _adapt(self, results: Sequence[Dict[str, Any]], retry_allowed: bool) -> Dict[str, Any]:
        evidence = {
            "feature": self.component,
            "retry_allowed": retry_allowed,
            "cases": [
                {"id": r["id"], "kind": r["kind"], "question": r["question"], "passed": r["check"]["passed"],
                 "reasons": r["check"]["reasons"], "status": r["observation"].get("status"),
                 "confidence": r["observation"].get("confidence"),
                 "cited_sections": [f"{c['source']} > {c['section']}" for c in r["observation"].get("citations", [])],
                 "top_score": (r["observation"].get("retrieval") or {}).get("top_score")}
                for r in results
            ],
        }
        result, error = self._model(f"{load_prompt('rag_adapt.txt')}\n\nValidation results:\n{json.dumps(evidence, indent=2)}")
        if error:
            self.log(f"ADAPT   unavailable: {error}")
        return {"result": result, "error": error}

    def _retries(self, adapt: Optional[Dict[str, Any]], results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Accept only rephrasings of failed answerable cases."""

        failed = {r["id"]: r for r in results if r["kind"] == "answerable" and not r["check"]["passed"]}
        retries, seen = [], set()
        for item in (adapt or {}).get("retry") or []:
            if not isinstance(item, dict) or item.get("id") not in failed or item["id"] in seen:
                continue
            question = _clean_question(item.get("question"))
            if not question or question.lower() == failed[item["id"]]["question"].lower():
                continue
            seen.add(item["id"])
            retries.append({"id": f"{item['id']}-retry", "retry_of": item["id"], "kind": "answerable",
                            "question": question, "origin": "adapt"})
        return retries

    def run(self) -> Dict[str, Any]:
        sources = self.rag.sources(self.component)
        if "error" in sources:
            raise ValueError(f"RAG server refused feature {self.component}: {sources['error'].get('message')}")
        documents = sources.get("documents") or []
        self.log(f"PLAN    {self.component}: {len(documents)} indexed document(s)")
        plan, plan_error = (None, None)
        if documents and not self.user_questions:
            plan, plan_error = self._model(build_plan_prompt(self.component, documents, self.questions))
            if plan_error:
                self.log(f"PLAN    model unavailable, using section-based questions: {plan_error}")
        cases = build_cases(plan, documents, self.questions, self.user_questions)

        rounds = []
        results = self._act(cases, 1)
        final = {r["id"]: r for r in results}
        for number in range(1, MAX_ROUNDS + 1):
            retry_allowed = number < MAX_ROUNDS and any(not r["check"]["passed"] for r in final.values())
            self.log(f"ADAPT   round {number}")
            adapt = self._adapt(list(final.values()), retry_allowed)
            rounds.append({"round": number, "results": results, "adapt": adapt})
            retries = self._retries(adapt["result"], results) if retry_allowed else []
            if not retries:
                break
            results = self._act(retries, number + 1)
            for retry in results:
                final[retry["retry_of"]] = {**retry, "id": retry["retry_of"]}

        failed = [case_id for case_id, r in final.items() if not r["check"]["passed"]]
        notes = [] if any(r["kind"] == "answerable" for r in final.values()) else [
            f"no answerable cases: add documents to ai-services/rag-server/knowledge/{self.component}/"]
        verdict = "pass" if not failed and not notes else "fail"
        self.log(f"CHECK   {len(final) - len(failed)}/{len(final)} case(s) passed -> {verdict.upper()}"
                 + (f" ({notes[0]})" if notes else ""))
        return {
            "documents": documents,
            "plan": plan,
            "plan_error": plan_error,
            "cases": cases,
            "rounds": rounds,
            "summary": {
                "verdict": verdict,
                "cases": len(final),
                "passed": len(final) - len(failed),
                "failed": failed,
                "notes": notes,
                "final": [{"id": case_id, "kind": r["kind"], "question": r["question"],
                           "status": r["observation"].get("status"),
                           "confidence": r["observation"].get("confidence"),
                           "citations": [f"{c['source']} > {c['section']}"
                                         for c in r["observation"].get("citations", [])],
                           "passed": r["check"]["passed"], "reasons": r["check"]["reasons"],
                           "retried": "retry_of" in r}
                          for case_id, r in final.items()],
            },
        }


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def _md_json(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(record: Dict[str, Any]) -> str:
    summary = record["summary"]
    rows = ["| Case | Kind | Question | Status | Confidence | Citations | Check |", "|---|---|---|---|---|---|---|"]
    for case in summary["final"]:
        check = "PASS" if case["passed"] else "FAIL: " + "; ".join(case["reasons"])
        rows.append("| " + " | ".join(_cell(v) for v in (
            case["id"] + (" (retried)" if case["retried"] else ""), case["kind"], case["question"],
            case["status"], case["confidence"], "<br>".join(case["citations"]) or "—", check)) + " |")
    parts = [
        "# Release 1 Agentic Loop Evidence — RAG Validation Mode",
        f"- Run: `{record['run_id']}`\n"
        f"- Component: `{record['component']}`\n"
        f"- Loop model: `{record['model']}`\n"
        f"- RAG server: `{record['rag_url']}`\n"
        f"- Verdict: **{summary['verdict'].upper()}** ({summary['passed']}/{summary['cases']} cases passed)",
        "## Summary\n\n" + "\n".join(rows)
        + "".join(f"\n\n> Note: {note}" for note in summary["notes"]),
        "## PLAN\n\nIndexed documents:\n\n" + _md_json(
            [{"source": d["source"], "sections": d["sections"]} for d in record["documents"]])
        + "\n\nModel plan:\n\n" + _md_json({"result": record["plan"], "error": record["plan_error"]})
        + "\n\nValidation cases:\n\n" + _md_json(record["cases"]),
    ]
    for round_record in record["rounds"]:
        parts.append(f"## Round {round_record['round']} — ACT / OBSERVE / CHECK\n\n" + _md_json(
            [{k: r[k] for k in ("id", "kind", "question", "origin", "observation", "check")}
             for r in round_record["results"]]))
        parts.append(f"## Round {round_record['round']} — ADAPT\n\n" + _md_json(round_record["adapt"]))
    return "\n\n".join(parts) + "\n"


def save_evidence(record: Dict[str, Any], logs_dir: Path) -> Dict[str, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    base = logs_dir / f"{record['run_id']}-rag-validation"
    json_path, md_path = Path(f"{base}.json"), Path(f"{base}.md")
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(record), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Release 1 RAG validation mode of the shared agentic loop."
    )
    parser.add_argument("--student", required=True, help="Student number or student-N (the feature)")
    parser.add_argument("--rag-url", default=os.environ.get("HOMS_RAG_URL", DEFAULT_RAG_URL))
    parser.add_argument("--questions", type=int, default=DEFAULT_QUESTIONS,
                        help=f"Answerable questions to generate (1-{MAX_QUESTIONS})")
    parser.add_argument("--question", action="append", dest="user_questions", default=[],
                        help="Use this answerable question instead of generating them (repeatable)")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    parser.add_argument("--ollama-timeout", type=float, default=DEFAULT_OLLAMA_TIMEOUT)
    parser.add_argument("--rag-timeout", type=float, default=DEFAULT_RAG_TIMEOUT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--logs-dir", type=Path,
                        help="Evidence destination; defaults to docs/agent-logs/student-N")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        component = normalise_student(args.student)
        loop = RagValidationLoop(
            rag=RagClient(args.rag_url, args.rag_timeout),
            generate=lambda prompt: ollama_generate_json(
                prompt, model=args.model, base_url=args.ollama_url, timeout=args.ollama_timeout),
            component=component,
            questions=args.questions,
            user_questions=args.user_questions,
        )
        run_id = _run_id()
        result = loop.run()
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except RagUnavailable as error:
        print(str(error), file=sys.stderr)
        return 3

    record = {
        "schema_version": 1,
        "release": "R1",
        "mode": "rag",
        "run_id": run_id,
        "component": component,
        "model": args.model,
        "ollama_url": args.ollama_url,
        "rag_url": args.rag_url,
        **result,
    }
    logs_dir = (args.logs_dir.resolve() if args.logs_dir
                else args.repo_root.resolve() / "docs" / "agent-logs" / component)
    paths = save_evidence(record, logs_dir)
    print(f"EVIDENCE {paths['markdown']}")
    print(f"EVIDENCE {paths['json']}")

    if result["summary"]["verdict"] != "pass":
        return 1
    model_errors = [result["plan_error"], *(r["adapt"]["error"] for r in result["rounds"])]
    return 2 if any(model_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
