"""Common agent prompt and validation behaviour."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable

from model_client import ModelClient, ModelError, validate_agent_output
from models import GenerationResult


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


class StructuredAgent:
    role = ""
    prompt_file = ""
    required_fields: Iterable[str] = ()

    def __init__(self, model_client: ModelClient):
        self.model_client = model_client

    def run(self, context: Dict[str, Any]) -> GenerationResult:
        prompt = (PROMPT_DIR / self.prompt_file).read_text(encoding="utf-8").strip()
        result = self.model_client.generate(
            role=self.role, prompt=prompt, context=context
        )
        try:
            semantic_validation = validate_agent_output(
                self.role, result.output, context
            )
        except ModelError as error:
            raise ModelError(
                str(error),
                attempts=result.attempts,
                provider=result.provider,
                model=result.model,
                validation_type=error.validation_type,
                semantic_validation=error.semantic_validation,
            ) from error
        missing = [field for field in self.required_fields if field not in result.output]
        if missing:
            raise ModelError(
                f"{self.role} output missing required field(s): {', '.join(missing)}"
            )
        rejected_attempts = []
        for attempt in result.attempts:
            if attempt.get("valid"):
                continue
            rejected_attempts.append(
                {
                    "attempt": attempt.get("attempt"),
                    "validation_type": attempt.get("validation_type"),
                    "issue_codes": [
                        issue.get("code")
                        for issue in (attempt.get("semantic_validation") or {}).get(
                            "issues", []
                        )
                    ],
                }
            )
        semantic_validation = {
            **semantic_validation,
            "retry_triggered": len(result.attempts) > 1,
            "fallback_triggered": result.fallback_used,
            "rejected_attempts": rejected_attempts,
        }
        return replace(result, semantic_validation=semantic_validation)
