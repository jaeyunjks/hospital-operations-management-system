#!/usr/bin/env python3
"""Run the infrastructure-only Ollama JSON smoke test."""
from services.ai_client import run_prompt


if __name__ == "__main__":
    result = run_prompt("smoke", {"ok": bool})
    print(result.to_dict())
    raise SystemExit(0 if result.ok else 1)
