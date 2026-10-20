"""Shared helpers for the Release 2 review modes of the agentic loop.

The three Release 2 modes (``multi-agent``, ``testing``, ``cloud``) follow the
same shape as the Release 1 RAG mode:

    PLAN -> ACT -> OBSERVE -> CHECK -> ADAPT

PLAN lists the evidence to collect and the checks to apply, ACT collects the
evidence, OBSERVE parses it, CHECK applies deterministic rules and ADAPT asks
the model for a review and recommendations. The verdict always comes from
CHECK, never from the model. The model never edits code or evidence.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from agentic_loop import (  # noqa: E402, F401 - re-exported to the review modes
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_TIMEOUT,
    DEFAULT_OLLAMA_URL,
    DEFAULT_REPO_ROOT,
    load_prompt,
    normalise_student,
    ollama_generate_json,
    _iso_utc,
    _run_id,
    _utc_now,
)

GenerateFn = Callable[[str], Dict[str, Any]]
REQUIRED = "required"
ADVISORY = "advisory"


class EvidenceUnavailable(RuntimeError):
    """The evidence source (a server or deployed endpoint) could not be reached."""


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def check(check_id: str, title: str, passed: bool, detail: str = "",
          severity: str = REQUIRED, subject: Optional[str] = None) -> Dict[str, Any]:
    """One deterministic check result. Advisory checks never fail the verdict."""

    result = {"id": check_id, "title": title, "severity": severity,
              "passed": bool(passed), "detail": detail}
    if subject:
        result["subject"] = subject
    return result


def summarise_checks(checks: Sequence[Dict[str, Any]], notes: Sequence[str] = ()) -> Dict[str, Any]:
    failed = [c for c in checks if not c["passed"] and c["severity"] == REQUIRED]
    warnings = [c for c in checks if not c["passed"] and c["severity"] == ADVISORY]
    verdict = "pass" if checks and not failed and not notes else "fail"
    return {
        "verdict": verdict,
        "checks": len(checks),
        "passed": sum(1 for c in checks if c["passed"]),
        "failed": [_label(c) for c in failed],
        "warnings": [_label(c) for c in warnings],
        "notes": list(notes),
    }


def _label(result: Dict[str, Any]) -> str:
    return f"{result['id']}" + (f" [{result['subject']}]" if result.get("subject") else "")


def exit_code(summary: Dict[str, Any], model_errors: Iterable[Optional[str]]) -> int:
    """0 pass, 1 a required check failed, 2 checks passed but the model was unavailable."""

    if summary["verdict"] != "pass":
        return 1
    return 2 if any(model_errors) else 0


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


def call_model(generate: GenerateFn, prompt: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        return generate(prompt), None
    except RuntimeError as error:
        return None, str(error)


def add_model_args(parser: Any) -> None:
    import os

    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    parser.add_argument("--ollama-timeout", type=float, default=DEFAULT_OLLAMA_TIMEOUT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--student", help="Student number or student-N; evidence then goes to "
                        "docs/agent-logs/student-N instead of docs/agent-logs/group")
    parser.add_argument("--logs-dir", type=Path, help="Evidence destination (overrides --student)")


def model_from_args(args: Any) -> GenerateFn:
    return lambda prompt: ollama_generate_json(
        prompt, model=args.model, base_url=args.ollama_url, timeout=args.ollama_timeout)


def default_logs_dir(args: Any) -> Path:
    if args.logs_dir:
        return args.logs_dir.resolve()
    folder = normalise_student(args.student) if args.student else "group"
    return args.repo_root.resolve() / "docs" / "agent-logs" / folder


# --------------------------------------------------------------------------- #
# Evidence files
# --------------------------------------------------------------------------- #


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error


def bounded(text: str, limit: int = 4000) -> Dict[str, Any]:
    return {"text": text[:limit], "truncated": len(text) > limit, "original_characters": len(text)}


def md_json(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def cell(value: Any) -> str:
    text = "-" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def checks_table(checks: Sequence[Dict[str, Any]]) -> str:
    rows = ["| Check | Subject | Severity | Result | Detail |", "|---|---|---|---|---|"]
    for c in checks:
        result = "PASS" if c["passed"] else ("WARN" if c["severity"] == ADVISORY else "FAIL")
        rows.append("| " + " | ".join(cell(v) for v in (
            f"{c['id']} {c['title']}", c.get("subject"), c["severity"], result, c["detail"])) + " |")
    return "\n".join(rows)


def review_section(adapt: Dict[str, Any]) -> str:
    result = adapt.get("result") or {}
    if adapt.get("error"):
        return f"Model review unavailable: {adapt['error']}"
    lines = [f"**Assessment:** {cell(result.get('assessment'))}"]
    for item in result.get("recommendations") or []:
        if isinstance(item, dict):
            lines.append(f"- **{cell(item.get('priority', '-'))}** {cell(item.get('target'))}: "
                         f"{cell(item.get('action'))}")
        else:
            lines.append(f"- {cell(item)}")
    lines.append(f"\n**Next action:** {cell(result.get('next_action'))}")
    return "\n".join(lines)


def save_review(record: Dict[str, Any], logs_dir: Path, suffix: str,
                render: Callable[[Dict[str, Any]], str]) -> Dict[str, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    base = logs_dir / f"{record['run_id']}-{suffix}"
    json_path, md_path = Path(f"{base}.json"), Path(f"{base}.md")
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render(record), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def student_from_text(text: str) -> Optional[str]:
    match = re.search(r"student[-_]?([1-9][0-9]*)", text, re.IGNORECASE)
    return f"student-{int(match.group(1))}" if match else None


def parse_assignment(value: str) -> Tuple[Optional[str], Path]:
    """Parse ``student-4=path`` / ``4=path`` / ``path`` (student taken from the path)."""

    if "=" in value:
        key, path = value.split("=", 1)
        return normalise_student(key), Path(path)
    return student_from_text(value), Path(value)


def students_list(value: str) -> List[str]:
    return [normalise_student(part) for part in value.split(",") if part.strip()]
