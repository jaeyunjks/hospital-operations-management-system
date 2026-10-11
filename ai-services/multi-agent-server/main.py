#!/usr/bin/env python3
"""Terminal entry point for the shared, non-containerised R2 workflow."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from agents.planner import Planner
from agents.reviewer import Reviewer
from agents.worker import Worker
from audit import AuditStore
from model_client import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    DeterministicModelClient,
    FallbackModelClient,
    ModelClient,
    OllamaModelClient,
)
from models import HumanDecision, HumanReview, utc_now
from orchestrator import MultiAgentOrchestrator


SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SERVICE_DIR.parents[1]
DEFAULT_EVIDENCE = SERVICE_DIR / "samples" / "staff-shortage.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "reports" / "multi-agent"
DEFAULT_TASK = "Resolve a staffing shortage for the Emergency evening shift."


def load_evidence(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"could not read evidence file: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"evidence file is not valid JSON: {error}") from error
    if not isinstance(value, dict) or not value:
        raise ValueError("evidence file must contain a non-empty JSON object")
    return value


def build_model_client(args: argparse.Namespace) -> ModelClient:
    deterministic = DeterministicModelClient()
    if args.model_mode == "deterministic":
        return deterministic
    ollama = OllamaModelClient(
        model=args.model, base_url=args.ollama_url, timeout=args.ollama_timeout
    )
    if args.model_mode == "auto":
        return FallbackModelClient(ollama, deterministic)
    return ollama


def request_human_review(_record: Dict[str, Any]) -> HumanReview:
    print("\n[HUMAN REVIEW]")
    print("A decision is required. No operational mutation has been performed.")
    while True:
        raw = input("Approve / Correct / Reject / Partially Accept: ")
        try:
            decision = HumanDecision.parse(raw)
            break
        except ValueError as error:
            print(error)
    comments = input("Comments or correction (optional): ").strip()
    if decision is HumanDecision.CORRECT and not comments:
        while not comments:
            comments = input("Correction details are required for Correct: ").strip()
    return HumanReview(decision=decision, comments=comments, timestamp=utc_now())


def print_stage(stage: str, payload: Dict[str, Any]) -> None:
    if stage == "workflow_started":
        print("[MULTI-AGENT] Workflow started")
        print(f"Workflow ID: {payload['workflow_id']}")
        return
    if stage in {"planner", "worker", "reviewer"}:
        print(f"\n[{stage.upper()}]")
        execution = payload["execution"]
        mode = execution["provider"]
        if execution.get("fallback_used"):
            mode += f" (fallback: {execution['fallback_reason']})"
        print(f"Execution: {mode}")
        print(json.dumps(payload["output"], indent=2, ensure_ascii=False))
        return
    if stage == "human_review_required":
        print(f"\nAudit checkpoint saved: {payload['audit_path']}")
        return
    if stage == "workflow_completed":
        print("\n[MULTI-AGENT] Workflow completed")
        print(json.dumps(payload, indent=2, ensure_ascii=False))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Planner -> Worker -> Reviewer -> Human Review locally."
    )
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument(
        "--model-mode",
        choices=("deterministic", "ollama", "auto"),
        default="deterministic",
        help=(
            "deterministic: repeatable no-network fallback (default); ollama: require "
            "live Ollama; auto: try Ollama and record any deterministic fallback"
        ),
    )
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL)
    )
    parser.add_argument("--ollama-timeout", type=float, default=120.0)
    parser.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        evidence = load_evidence(args.evidence.resolve())
        client = build_model_client(args)
        orchestrator = MultiAgentOrchestrator(
            planner=Planner(client),
            worker=Worker(client),
            reviewer=Reviewer(client),
            audit_store=AuditStore(args.reports_dir.resolve()),
            on_stage=print_stage,
        )
        _record, audit_path = orchestrator.run(
            task=args.task,
            evidence=evidence,
            request_human_review=request_human_review,
        )
    except (ValueError, RuntimeError) as error:
        print(f"Configuration/workflow error: {error}", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print(
            "\nHuman review was not recorded; workflow remains awaiting_human_review.",
            file=sys.stderr,
        )
        return 2
    print(f"Audit saved: {audit_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
