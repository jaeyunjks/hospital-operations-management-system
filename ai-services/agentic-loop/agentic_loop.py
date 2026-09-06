#!/usr/bin/env python3
"""Shared Release 0 Plan -> Act -> Observe -> Adapt validation loop.

The loop uses one Ollama model for PLAN and ADAPT. ACT is always the exact
validation command supplied by the user, executed without a shell in the
selected ``student-x`` directory. OBSERVE records the command's real output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_TIMEOUT = 120.0
DEFAULT_COMMAND_TIMEOUT = 600.0
DEFAULT_EVIDENCE_LIMIT = 12_000

AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = AGENT_DIR.parents[1]
PROMPT_DIR = AGENT_DIR / "prompts"

_IGNORED_INVENTORY_PARTS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "htmlcov",
    "node_modules",
    "output",
    "playwright-cli",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_id(value: Optional[datetime] = None) -> str:
    return (value or _utc_now()).strftime("%Y%m%dT%H%M%S.%fZ")


def normalise_student(value: str) -> str:
    """Return a canonical ``student-N`` component name."""

    match = re.fullmatch(r"(?:student-)?([1-9][0-9]*)", value.strip().lower())
    if not match:
        raise ValueError("student must be a positive number or student-N")
    return f"student-{int(match.group(1))}"


def resolve_component(repo_root: Path, student: str) -> Path:
    component = (repo_root / normalise_student(student)).resolve()
    if component.parent != repo_root.resolve() or not component.is_dir():
        raise ValueError(f"component does not exist: {component}")
    return component


def component_inventory(component: Path, limit: int = 200) -> List[str]:
    """Return a small, deterministic file inventory without reading content."""

    files: List[str] = []
    for path in sorted(component.rglob("*")):
        relative = path.relative_to(component)
        if any(part in _IGNORED_INVENTORY_PARTS for part in relative.parts):
            continue
        if path.is_file():
            files.append(relative.as_posix())
            if len(files) == limit:
                break
    return files


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def ollama_generate_json(
    prompt: str,
    *,
    model: str,
    base_url: str,
    timeout: float,
) -> Dict[str, Any]:
    """Generate one deterministic JSON object through Ollama."""

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"Ollama request failed: {error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama returned an invalid response envelope") from error

    generated = envelope.get("response")
    if not isinstance(generated, str) or not generated.strip():
        raise RuntimeError("Ollama response did not contain generated text")
    try:
        result = json.loads(generated)
    except json.JSONDecodeError as error:
        raise RuntimeError("Llama returned invalid JSON") from error
    if not isinstance(result, dict):
        raise RuntimeError("Llama output was not a JSON object")
    return result


def build_plan_prompt(
    *,
    component_name: str,
    task: str,
    command: str,
    inventory: Sequence[str],
) -> str:
    context = {
        "component": component_name,
        "task": task,
        "validation_command": command,
        "file_inventory": list(inventory),
    }
    return f"{load_prompt('plan.txt')}\n\nComponent facts:\n{json.dumps(context, indent=2)}"


def _text_from_timeout(value: Any) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def run_validation(command: str, *, cwd: Path, timeout: float) -> Dict[str, Any]:
    """Run the requested command directly and capture its full evidence."""

    argv = shlex.split(command)
    if not argv:
        raise ValueError("validation command must not be empty")

    started_at = _utc_now()
    start = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as error:
        return_code = 124
        stdout = _text_from_timeout(error.stdout)
        stderr = _text_from_timeout(error.stderr)
        timed_out = True

    finished_at = _utc_now()
    return {
        "command": command,
        "argv": argv,
        "cwd": str(cwd),
        "started_at": _iso_utc(started_at),
        "finished_at": _iso_utc(finished_at),
        "duration_seconds": round(time.monotonic() - start, 3),
        "return_code": return_code,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
    }


def _bounded(value: str, limit: int) -> Dict[str, Any]:
    if len(value) <= limit:
        return {"text": value, "truncated": False, "original_characters": len(value)}
    return {
        "text": value[:limit],
        "truncated": True,
        "original_characters": len(value),
    }


def build_adapt_prompt(
    *,
    plan: Optional[Dict[str, Any]],
    plan_error: Optional[str],
    observation: Dict[str, Any],
    evidence_limit: int,
) -> str:
    evidence = {
        "plan": plan,
        "plan_error": plan_error,
        "command": observation["command"],
        "cwd": observation["cwd"],
        "return_code": observation["return_code"],
        "timed_out": observation["timed_out"],
        "duration_seconds": observation["duration_seconds"],
        "stdout": _bounded(observation["stdout"], evidence_limit),
        "stderr": _bounded(observation["stderr"], evidence_limit),
    }
    return f"{load_prompt('adapt.txt')}\n\nCaptured evidence:\n{json.dumps(evidence, indent=2)}"


def _markdown_json(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def render_markdown(record: Dict[str, Any]) -> str:
    observation = record["observe"]
    return "\n\n".join([
        "# Release 0 Agentic Loop Evidence",
        f"- Run: `{record['run_id']}`\n"
        f"- Student component: `{record['component']}`\n"
        f"- Model: `{record['model']}`\n"
        f"- Task: {record['task']}\n"
        f"- Validation command: `{observation['command']}`\n"
        f"- Command exit code: `{observation['return_code']}`",
        "## PLAN\n\n" + _markdown_json({
            "result": record["plan"],
            "error": record["plan_error"],
        }),
        "## ACT\n\n" + _markdown_json({
            "command": observation["command"],
            "argv": observation["argv"],
            "cwd": observation["cwd"],
        }),
        "## OBSERVE\n\n" + _markdown_json(observation),
        "## ADAPT\n\n" + _markdown_json({
            "result": record["adapt"],
            "error": record["adapt_error"],
        }),
        "",
    ])


def save_evidence(record: Dict[str, Any], logs_dir: Path) -> Dict[str, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    base = logs_dir / f"{record['run_id']}-agentic-loop"
    json_path = Path(f"{base}.json")
    markdown_path = Path(f"{base}.md")
    json_path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(record), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the shared Release 0 Plan -> Act -> Observe -> Adapt loop."
    )
    parser.add_argument("--student", required=True, help="Student number or student-N")
    parser.add_argument("--command", required=True, help="Validation command to run")
    parser.add_argument(
        "--task",
        default="Validate the selected component and recommend the next evidence-based action.",
    )
    parser.add_argument(
        "--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
    )
    parser.add_argument(
        "--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL)
    )
    parser.add_argument("--ollama-timeout", type=float, default=DEFAULT_OLLAMA_TIMEOUT)
    parser.add_argument("--command-timeout", type=float, default=DEFAULT_COMMAND_TIMEOUT)
    parser.add_argument("--evidence-limit", type=int, default=DEFAULT_EVIDENCE_LIMIT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument(
        "--logs-dir",
        type=Path,
        help="Evidence destination; defaults to docs/agent-logs/student-N",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        component = resolve_component(repo_root, args.student)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    component_name = component.name
    logs_dir = (
        args.logs_dir.resolve()
        if args.logs_dir
        else repo_root / "docs" / "agent-logs" / component_name
    )
    run_id = _run_id()
    inventory = component_inventory(component)

    print(f"PLAN  {component_name} with {args.model}")
    plan: Optional[Dict[str, Any]] = None
    plan_error: Optional[str] = None
    try:
        plan = ollama_generate_json(
            build_plan_prompt(
                component_name=component_name,
                task=args.task,
                command=args.command,
                inventory=inventory,
            ),
            model=args.model,
            base_url=args.ollama_url,
            timeout=args.ollama_timeout,
        )
    except RuntimeError as error:
        plan_error = str(error)
        print(f"PLAN  unavailable: {plan_error}", file=sys.stderr)

    print(f"ACT   {args.command}")
    try:
        observation = run_validation(
            args.command,
            cwd=component,
            timeout=args.command_timeout,
        )
    except (OSError, ValueError) as error:
        now = _iso_utc(_utc_now())
        observation = {
            "command": args.command,
            "argv": [],
            "cwd": str(component),
            "started_at": now,
            "finished_at": now,
            "duration_seconds": 0.0,
            "return_code": 127,
            "timed_out": False,
            "stdout": "",
            "stderr": str(error),
        }

    print(
        f"OBSERVE exit={observation['return_code']} "
        f"duration={observation['duration_seconds']}s"
    )
    print(f"ADAPT {component_name} with {args.model}")
    adapt: Optional[Dict[str, Any]] = None
    adapt_error: Optional[str] = None
    try:
        adapt = ollama_generate_json(
            build_adapt_prompt(
                plan=plan,
                plan_error=plan_error,
                observation=observation,
                evidence_limit=args.evidence_limit,
            ),
            model=args.model,
            base_url=args.ollama_url,
            timeout=args.ollama_timeout,
        )
    except RuntimeError as error:
        adapt_error = str(error)
        print(f"ADAPT unavailable: {adapt_error}", file=sys.stderr)

    record = {
        "schema_version": 1,
        "release": "R0",
        "run_id": run_id,
        "component": component_name,
        "task": args.task,
        "model": args.model,
        "ollama_url": args.ollama_url,
        "file_inventory": inventory,
        "plan": plan,
        "plan_error": plan_error,
        "observe": observation,
        "adapt": adapt,
        "adapt_error": adapt_error,
    }
    paths = save_evidence(record, logs_dir)
    print(f"EVIDENCE {paths['markdown']}")
    print(f"EVIDENCE {paths['json']}")

    if observation["return_code"] != 0:
        return int(observation["return_code"])
    if plan_error or adapt_error:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
