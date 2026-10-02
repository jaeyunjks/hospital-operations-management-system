#!/usr/bin/env python3
"""Terminal client for validating the running shared HOMS MCP server.

Examples, from the repository root::

    python3 ai-services/mcp-server/cli.py tools
    python3 ai-services/mcp-server/cli.py call homs_echo '{"message": "terminal check"}'
    python3 ai-services/mcp-server/cli.py call homs_pharmacy_stock_alerts '{"alert_type": "low_stock"}' --expect ok
    python3 ai-services/mcp-server/cli.py call homs_pharmacy_order_alerts '{"alert_type": "bogus"}' --expect error

Uses the official MCP Python SDK over Streamable HTTP, exactly like the
feature backends do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict

DEFAULT_URL = os.environ.get("HOMS_MCP_URL", "http://127.0.0.1:8000/mcp")


def _root_cause(error: BaseException) -> BaseException:
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


def _summary(payload: Dict[str, Any]) -> str:
    """One readable line per tool result, followed by the full structured result."""

    data = payload.get("data") or {}
    if not payload.get("ok"):
        error = payload.get("error") or {}
        return f"ERROR {error.get('code')}: {error.get('message')}"
    if "counts" in data:
        counts = ", ".join(f"{key}={value}" for key, value in data["counts"].items())
        lists = ", ".join(f"{key}: {len(value)} rows" for key, value in data.items()
                          if isinstance(value, list))
        return f"OK counts[{counts}] lists[{lists or 'none requested'}]"
    return f"OK {json.dumps(data)}"


async def run(url: str, command: str, tool: str | None, arguments: Dict[str, Any]) -> Dict[str, Any] | None:
    from mcp import Client

    async with Client(url, read_timeout_seconds=30) as client:
        if command == "tools":
            listing = await client.list_tools()
            for item in listing.tools:
                properties = list((item.input_schema or {}).get("properties", {}))
                print(f"- {item.name}: {item.title or ''}  (arguments: {', '.join(properties) or 'none'})")
            return None
        result = await client.call_tool(tool, arguments)
        payload = result.structured_content or {"ok": False, "error": {"code": "no_structured_content"}}
        return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"MCP endpoint (default {DEFAULT_URL})")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tools", help="list registered tools")
    call = commands.add_parser("call", help="call one tool")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON object of arguments")
    call.add_argument("--expect", choices=("ok", "error"), help="exit 1 unless the result matches")
    call.add_argument("--json", action="store_true", help="also print the full structured result")
    args = parser.parse_args(argv)

    arguments: Dict[str, Any] = {}
    if args.command == "call":
        try:
            arguments = json.loads(args.arguments)
        except json.JSONDecodeError as error:
            print(f"Arguments must be a JSON object: {error}", file=sys.stderr)
            return 2
        if not isinstance(arguments, dict):
            print("Arguments must be a JSON object", file=sys.stderr)
            return 2
    try:
        payload = asyncio.run(run(args.url, args.command, getattr(args, "tool", None), arguments))
    except Exception as error:  # noqa: BLE001 - report transport failures plainly
        error = _root_cause(error)
        print(f"MCP server unavailable at {args.url}: {type(error).__name__}: {error}", file=sys.stderr)
        return 3
    if payload is None:
        return 0
    print(_summary(payload))
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    if args.expect and payload.get("ok") is not (args.expect == "ok"):
        print(f"EXPECTED {args.expect}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
