#!/usr/bin/env python3
"""Release 1 grounded agentic loop: Plan -> Act -> Observe -> Adapt over MCP.

Unlike the Release 0 loop (``agentic_loop.py``), ACT here is a tool call that
the model selects from the tools advertised by the shared HOMS MCP server.
The loop repeats until the model answers or the step budget is spent:

1. PLAN     the model reads the question and the discovered tool contracts.
2. ACT      the model requests one or more MCP tool calls.
3. OBSERVE  each call is validated, executed through MCP and recorded as-is.
4. ADAPT    the model either requests more evidence or gives a final answer
            that must cite the call IDs it relied on.

After the loop, a deterministic grounding check verifies the citations and
flags any number in the answer that does not appear in the cited evidence.
The model never receives database access and never edits code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence


DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_OLLAMA_TIMEOUT = 120.0
DEFAULT_TOOL_TIMEOUT = 30.0
DEFAULT_MAX_STEPS = 4
DEFAULT_MAX_TOOL_CALLS = 6
DEFAULT_EVIDENCE_LIMIT = 8_000
MAX_QUESTION_LENGTH = 1_000

AGENT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = AGENT_DIR.parents[1]
PROMPT_DIR = AGENT_DIR / "prompts"

_NUMBER_PATTERN = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_id(value: Optional[datetime] = None) -> str:
    return (value or _utc_now()).strftime("%Y%m%dT%H%M%S.%fZ")


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def normalise_student(value: str) -> str:
    match = re.fullmatch(r"(?:student-)?([1-9][0-9]*)", value.strip().lower())
    if not match:
        raise ValueError("student must be a positive number or student-N")
    return f"student-{int(match.group(1))}"


def _root_cause(error: BaseException) -> BaseException:
    """Unwrap anyio ExceptionGroups so CLI errors name the real failure."""

    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


def _bounded_json(value: Any, limit: int) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return json.dumps(
        {"truncated": True, "original_characters": len(text), "text": text[:limit]}
    )


# --------------------------------------------------------------------------- #
# Tool provider (MCP)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolSpec:
    """A tool contract as advertised by the MCP server."""

    name: str
    description: str
    input_schema: Dict[str, Any]

    def to_ollama(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


@dataclass
class ToolOutcome:
    """The unchanged result of one MCP tool call."""

    ok: bool
    payload: Any
    error: Optional[str] = None


class ToolProvider(Protocol):
    async def list_tools(self) -> List[ToolSpec]: ...

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> ToolOutcome: ...


class MCPToolProvider:
    """Discover and call tools on the shared HOMS MCP server.

    ``target`` is either the Streamable HTTP URL (normal use) or an in-process
    ``mcp.server.Server`` instance (tests). One connection is opened per
    operation so the loop holds no long-lived session state.
    """

    def __init__(self, target: Any, *, timeout: float = DEFAULT_TOOL_TIMEOUT):
        self.target = target
        self.timeout = timeout

    def _client(self):
        from mcp import Client  # imported lazily so unit tests need no MCP

        return Client(self.target, read_timeout_seconds=self.timeout)

    async def list_tools(self) -> List[ToolSpec]:
        async with self._client() as client:
            result = await client.list_tools()
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.input_schema or {}),
            )
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> ToolOutcome:
        async with self._client() as client:
            result = await client.call_tool(name, arguments)
        payload: Any = result.structured_content
        if payload is None:
            payload = [
                getattr(block, "text", None) for block in (result.content or [])
            ]
        return ToolOutcome(ok=not result.is_error, payload=payload)


# --------------------------------------------------------------------------- #
# Model client (Ollama chat with tool calling)
# --------------------------------------------------------------------------- #


ChatFn = Callable[[List[Dict[str, Any]], Optional[List[Dict[str, Any]]]], Dict[str, Any]]


def make_ollama_chat(*, model: str, base_url: str, timeout: float) -> ChatFn:
    """Return a blocking chat function bound to one Ollama model."""

    def chat(
        messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0},
        }
        if tools:
            payload["tools"] = tools
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/chat",
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
        message = envelope.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("Ollama response did not contain a message")
        return message

    return chat


# --------------------------------------------------------------------------- #
# Loop state
# --------------------------------------------------------------------------- #


@dataclass
class ToolCallRecord:
    call_id: str
    step: int
    tool: str
    arguments: Any
    status: str  # executed | rejected | failed
    ok: bool
    result: Any = None
    error: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""


@dataclass
class LoopResult:
    question: str
    tools: List[str]
    steps: List[Dict[str, Any]] = field(default_factory=list)
    calls: List[ToolCallRecord] = field(default_factory=list)
    final: Optional[Dict[str, Any]] = None
    raw_final: Optional[str] = None
    stop_reason: str = ""
    model_error: Optional[str] = None
    grounding: Dict[str, Any] = field(default_factory=dict)


def parse_tool_arguments(raw: Any) -> Dict[str, Any]:
    """Accept the object or JSON-string forms Ollama may emit."""

    if raw is None or raw == "":
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("tool arguments are not valid JSON") from error
    if not isinstance(raw, dict):
        raise ValueError("tool arguments must be a JSON object")
    return raw


def parse_final_answer(content: str) -> Dict[str, Any]:
    """Parse the final JSON answer; tolerate code fences around it."""

    text = content.strip()
    fenced = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"answer": content.strip(), "cited_calls": [], "limitations": "unstructured"}
    if not isinstance(data, dict):
        return {"answer": content.strip(), "cited_calls": [], "limitations": "unstructured"}
    cited = data.get("cited_calls", [])
    if not isinstance(cited, list):
        cited = []
    return {
        "answer": str(data.get("answer", "")).strip(),
        "cited_calls": [str(item) for item in cited],
        "limitations": str(data.get("limitations", "")).strip(),
    }


def _numbers(text: str) -> List[str]:
    return _NUMBER_PATTERN.findall(text)


def _canonical_number(value: str) -> str:
    try:
        number = float(value)
    except ValueError:
        return value
    return str(int(number)) if number.is_integer() else repr(number)


def check_grounding(final: Optional[Dict[str, Any]], calls: Sequence[ToolCallRecord]) -> Dict[str, Any]:
    """Deterministically verify citations and numeric claims.

    status:
      grounded    every cited call exists and succeeded, and every number in
                  the answer appears in the cited evidence
      partial     at least one valid citation, but some citation is invalid or
                  some number is unsupported
      ungrounded  no valid citation (or no answer at all)
    """

    if not final or not final.get("answer"):
        return {"status": "ungrounded", "reason": "no final answer",
                "valid_citations": [], "invalid_citations": [], "unsupported_numbers": []}

    by_id = {call.call_id: call for call in calls}
    valid = [cid for cid in final["cited_calls"] if cid in by_id and by_id[cid].ok]
    invalid = [cid for cid in final["cited_calls"] if cid not in valid]

    evidence_numbers = set()
    for cid in valid:
        call = by_id[cid]
        blob = json.dumps(call.result, ensure_ascii=False) + json.dumps(call.arguments)
        evidence_numbers.update(_canonical_number(n) for n in _numbers(blob))
    # Call IDs themselves ("call-2") are not factual claims.
    answer_text = re.sub(r"call-\d+", "", final["answer"])
    unsupported = sorted({
        n for n in _numbers(answer_text) if _canonical_number(n) not in evidence_numbers
    })

    if not valid:
        status, reason = "ungrounded", "no valid citation to a successful tool call"
    elif invalid or unsupported:
        status, reason = "partial", "some citations or numbers are not supported by evidence"
    else:
        status, reason = "grounded", "all citations and numbers are supported by evidence"
    return {"status": status, "reason": reason, "valid_citations": valid,
            "invalid_citations": invalid, "unsupported_numbers": unsupported}


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


class GroundedAgenticLoop:
    def __init__(
        self,
        *,
        chat: ChatFn,
        provider: ToolProvider,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        allowed_tools: Optional[Sequence[str]] = None,
        evidence_limit: int = DEFAULT_EVIDENCE_LIMIT,
        log: Callable[[str], None] = print,
    ):
        if max_steps < 1 or max_tool_calls < 1:
            raise ValueError("max_steps and max_tool_calls must be at least 1")
        self.chat = chat
        self.provider = provider
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.allowed_tools = set(allowed_tools) if allowed_tools else None
        self.evidence_limit = evidence_limit
        self.log = log

    async def _discover(self) -> List[ToolSpec]:
        tools = await self.provider.list_tools()
        if self.allowed_tools is not None:
            tools = [tool for tool in tools if tool.name in self.allowed_tools]
        return tools

    async def _chat(self, messages, tools) -> Dict[str, Any]:
        return await asyncio.to_thread(self.chat, messages, tools)

    async def _execute(
        self, step: int, call_id: str, raw_call: Dict[str, Any], known: Dict[str, ToolSpec]
    ) -> ToolCallRecord:
        function = raw_call.get("function") or {}
        name = str(function.get("name", ""))
        started = _iso_utc(_utc_now())
        record = ToolCallRecord(call_id=call_id, step=step, tool=name,
                                arguments=function.get("arguments"), status="rejected",
                                ok=False, started_at=started)
        if name not in known:
            record.error = f"tool '{name}' is not available to this loop"
        else:
            try:
                arguments = parse_tool_arguments(function.get("arguments"))
                record.arguments = arguments
                outcome = await self.provider.call_tool(name, arguments)
                record.status = "executed"
                record.ok = outcome.ok
                record.result = outcome.payload
                record.error = outcome.error
            except ValueError as error:
                record.error = str(error)
            except Exception as error:  # transport failure is evidence, not a crash
                error = _root_cause(error)
                record.status = "failed"
                record.error = f"{type(error).__name__}: {error}"
        record.finished_at = _iso_utc(_utc_now())
        return record

    def _tool_message(self, record: ToolCallRecord) -> Dict[str, Any]:
        body = {"call_id": record.call_id, "tool": record.tool, "ok": record.ok,
                "status": record.status, "result": record.result, "error": record.error}
        return {"role": "tool", "tool_name": record.tool,
                "content": _bounded_json(body, self.evidence_limit)}

    async def run(self, question: str) -> LoopResult:
        question = question.strip()
        if not question or len(question) > MAX_QUESTION_LENGTH:
            raise ValueError(f"question must be 1-{MAX_QUESTION_LENGTH} characters")

        tools = await self._discover()
        known = {tool.name: tool for tool in tools}
        result = LoopResult(question=question, tools=sorted(known))
        ollama_tools = [tool.to_ollama() for tool in tools]
        self.log(f"PLAN    {len(tools)} MCP tool(s): {', '.join(sorted(known)) or 'none'}")

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": load_prompt("grounded_system.txt")},
            {"role": "user", "content": question},
        ]
        call_count = 0

        for step in range(1, self.max_steps + 1):
            budget_left = self.max_tool_calls - call_count
            offer_tools = ollama_tools if budget_left > 0 else None
            try:
                message = await self._chat(messages, offer_tools)
            except RuntimeError as error:
                result.model_error = str(error)
                result.stop_reason = "model_unavailable"
                self.log(f"ADAPT   model unavailable: {error}")
                break

            tool_calls = message.get("tool_calls") or []
            content = message.get("content") or ""
            assistant = {"role": "assistant", "content": content}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            messages.append(assistant)
            step_log: Dict[str, Any] = {"step": step, "model_content": content, "call_ids": []}

            if not tool_calls:
                result.raw_final = content
                result.final = parse_final_answer(content)
                result.stop_reason = "answered"
                result.steps.append(step_log)
                self.log(f"ADAPT   step {step}: final answer")
                break

            for raw_call in tool_calls[: max(budget_left, 0)]:
                call_count += 1
                record = await self._execute(step, f"call-{call_count}", raw_call, known)
                result.calls.append(record)
                step_log["call_ids"].append(record.call_id)
                messages.append(self._tool_message(record))
                self.log(f"ACT     {record.call_id} {record.tool} {json.dumps(record.arguments)}")
                self.log(f"OBSERVE {record.call_id} status={record.status} ok={record.ok}")
            result.steps.append(step_log)
        else:
            result.stop_reason = "step_budget_exhausted"

        if result.final is None and result.model_error is None:
            # Budget spent while still calling tools: ask once for an answer
            # from the evidence already gathered, with tools withdrawn.
            messages.append({"role": "user", "content": load_prompt("grounded_finalise.txt")})
            try:
                message = await self._chat(messages, None)
                result.raw_final = message.get("content") or ""
                result.final = parse_final_answer(result.raw_final)
                self.log("ADAPT   forced final answer after budget")
            except RuntimeError as error:
                result.model_error = str(error)

        result.grounding = check_grounding(result.final, result.calls)
        self.log(f"GROUND  {result.grounding['status']}: {result.grounding['reason']}")
        return result


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def build_record(result: LoopResult, *, run_id: str, component: str, model: str,
                 ollama_url: str, mcp_url: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "release": "R1",
        "run_id": run_id,
        "component": component,
        "model": model,
        "ollama_url": ollama_url,
        "mcp_url": mcp_url,
        "question": result.question,
        "discovered_tools": result.tools,
        "steps": result.steps,
        "tool_calls": [call.__dict__ for call in result.calls],
        "final": result.final,
        "raw_final": result.raw_final,
        "stop_reason": result.stop_reason,
        "model_error": result.model_error,
        "grounding": result.grounding,
    }


def _md_json(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def render_markdown(record: Dict[str, Any]) -> str:
    final = record["final"] or {}
    parts = [
        "# Release 1 Grounded Agentic Loop Evidence",
        f"- Run: `{record['run_id']}`\n"
        f"- Component: `{record['component']}`\n"
        f"- Model: `{record['model']}`\n"
        f"- MCP server: `{record['mcp_url']}`\n"
        f"- Question: {record['question']}\n"
        f"- Stop reason: `{record['stop_reason']}`\n"
        f"- Grounding: **{record['grounding'].get('status')}**",
        "## PLAN\n\nDiscovered tools:\n\n" + _md_json(record["discovered_tools"]),
        "## ACT / OBSERVE\n\n" + _md_json(record["tool_calls"]),
        "## ADAPT (final answer)\n\n" + _md_json(final),
        "## Grounding check\n\n" + _md_json(record["grounding"]),
    ]
    if record["model_error"]:
        parts.append(f"## Model error\n\n`{record['model_error']}`")
    return "\n\n".join(parts) + "\n"


def save_evidence(record: Dict[str, Any], logs_dir: Path) -> Dict[str, Path]:
    logs_dir.mkdir(parents=True, exist_ok=True)
    base = logs_dir / f"{record['run_id']}-grounded-loop"
    json_path, md_path = Path(f"{base}.json"), Path(f"{base}.md")
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(record), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Release 1 grounded Plan -> Act -> Observe -> Adapt loop over MCP."
    )
    parser.add_argument("--question", required=True, help="Operational question to answer")
    parser.add_argument("--student", required=True, help="Student number or student-N (log folder)")
    parser.add_argument("--mcp-url", default=os.environ.get("HOMS_MCP_URL", DEFAULT_MCP_URL))
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    parser.add_argument("--ollama-timeout", type=float, default=DEFAULT_OLLAMA_TIMEOUT)
    parser.add_argument("--tool-timeout", type=float, default=DEFAULT_TOOL_TIMEOUT)
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS)
    parser.add_argument("--tool", action="append", dest="tools",
                        help="Allow only this MCP tool (repeatable); default: all discovered")
    parser.add_argument("--evidence-limit", type=int, default=DEFAULT_EVIDENCE_LIMIT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--logs-dir", type=Path,
                        help="Evidence destination; defaults to docs/agent-logs/student-N")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        component = normalise_student(args.student)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    logs_dir = (args.logs_dir.resolve() if args.logs_dir
                else args.repo_root.resolve() / "docs" / "agent-logs" / component)

    loop = GroundedAgenticLoop(
        chat=make_ollama_chat(model=args.model, base_url=args.ollama_url,
                              timeout=args.ollama_timeout),
        provider=MCPToolProvider(args.mcp_url, timeout=args.tool_timeout),
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        allowed_tools=args.tools,
        evidence_limit=args.evidence_limit,
    )
    run_id = _run_id()
    try:
        result = asyncio.run(loop.run(args.question))
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        error = _root_cause(error)
        print(f"MCP server unavailable at {args.mcp_url}: {type(error).__name__}: {error}",
              file=sys.stderr)
        return 3

    record = build_record(result, run_id=run_id, component=component, model=args.model,
                          ollama_url=args.ollama_url, mcp_url=args.mcp_url)
    paths = save_evidence(record, logs_dir)
    if result.final:
        print(f"\nANSWER  {result.final['answer']}")
    print(f"EVIDENCE {paths['markdown']}")
    print(f"EVIDENCE {paths['json']}")

    if result.model_error:
        return 2
    return 0 if result.grounding.get("status") == "grounded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
