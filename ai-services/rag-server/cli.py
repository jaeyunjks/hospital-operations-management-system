#!/usr/bin/env python3
"""Terminal client for validating the running shared HOMS RAG server.

Examples, from the repository root::

    python3 ai-services/rag-server/cli.py health
    python3 ai-services/rag-server/cli.py sources --feature student-3
    python3 ai-services/rag-server/cli.py retrieve "When is stock low?" --feature student-3
    python3 ai-services/rag-server/cli.py query "When is stock low?" --feature student-3 --expect answered
    python3 ai-services/rag-server/cli.py query "What is the capital of France?" --expect insufficient_context
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULT_URL = os.environ.get("HOMS_RAG_URL", "http://127.0.0.1:8100")


def call(base_url: str, method: str, path: str, payload: Optional[Dict[str, Any]] = None,
         timeout: float = 180) -> Tuple[int, Dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except json.JSONDecodeError:
            return error.code, {"error": {"code": "http_error", "message": f"HTTP {error.code}"}}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        print(f"RAG server is not reachable at {base_url}: {error}", file=sys.stderr)
        sys.exit(3)


def _wrap(text: str, indent: str = "  ") -> str:
    return "\n".join(textwrap.fill(line, 96, initial_indent=indent, subsequent_indent=indent) if line.strip() else ""
                     for line in text.splitlines())


def print_query(body: Dict[str, Any]) -> None:
    retrieval = body["retrieval"]
    print(f"Question:   {body['question']}")
    print(f"Feature:    {body['feature'] or 'all'}")
    print(f"Status:     {body['status']}" + (f" ({body['reason']})" if body.get("reason") else ""))
    print(f"Confidence: {body['confidence']}")
    print(f"Retrieval:  top score {retrieval['top_score']} vs threshold {retrieval['threshold']}; "
          f"{retrieval['relevant']}/{retrieval['considered']} chunks relevant")
    print(f"Models:     {body['models']['embedding']} + {body['models']['generation']} "
          f"({body['duration_ms']} ms)")
    print("Answer:")
    print(_wrap(body["answer"]))
    if body["citations"]:
        print("Citations:")
        for citation in body["citations"]:
            print(f"  [{citation['id']}] {citation['source']} > {citation['section']} (score {citation['score']})")
            print(_wrap(f"\"{citation['snippet']}\"", "       "))


def print_retrieve(body: Dict[str, Any]) -> None:
    print(f"Question:  {body['question']}")
    print(f"Feature:   {body['feature'] or 'all'} (relevance threshold {body['threshold']})")
    for rank, result in enumerate(body["results"], start=1):
        flag = "relevant" if result["relevant"] else "below threshold"
        print(f"  {rank}. {result['score']:.4f} {flag:15} {result['source']} > {result['section']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"RAG server base URL (default {DEFAULT_URL})")
    parser.add_argument("--json", action="store_true", help="print the raw JSON response")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="show index, model and threshold status")
    sources = commands.add_parser("sources", help="list indexed knowledge documents")
    sources.add_argument("--feature")
    for name in ("retrieve", "query"):
        command = commands.add_parser(name, help=f"POST /{name}")
        command.add_argument("question")
        command.add_argument("--feature", help="student-1 .. student-5 (shared documents are always searched)")
        command.add_argument("--top-k", type=int)
        if name == "query":
            command.add_argument("--expect", choices=("answered", "insufficient_context"),
                                 help="exit 1 unless the response has this status")
    args = parser.parse_args(argv)

    if args.command == "health":
        status, body = call(args.url, "GET", "/health")
    elif args.command == "sources":
        status, body = call(args.url, "GET", "/sources" + (f"?feature={args.feature}" if args.feature else ""))
    else:
        payload = {"question": args.question}
        if args.feature:
            payload["feature"] = args.feature
        if args.top_k is not None:
            payload["top_k"] = args.top_k
        status, body = call(args.url, "POST", f"/{args.command}", payload)

    if args.json or "error" in body or args.command in ("health", "sources"):
        print(json.dumps(body, indent=2))
    elif args.command == "query":
        print_query(body)
    else:
        print_retrieve(body)

    if status >= 400:
        return 1
    if args.command == "query" and args.expect and body.get("status") != args.expect:
        print(f"EXPECTED status {args.expect} but got {body.get('status')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
