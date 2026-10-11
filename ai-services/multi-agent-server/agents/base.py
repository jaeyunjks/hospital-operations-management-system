"""Common agent prompt and validation behaviour."""

from __future__ import annotations

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
            validate_agent_output(self.role, result.output, context)
        except ModelError as error:
            raise ModelError(
                str(error),
                attempts=result.attempts,
                provider=result.provider,
                model=result.model,
            ) from error
        missing = [field for field in self.required_fields if field not in result.output]
        if missing:
            raise ModelError(
                f"{self.role} output missing required field(s): {', '.join(missing)}"
            )
        return result
