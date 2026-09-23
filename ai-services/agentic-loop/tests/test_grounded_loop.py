from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "grounded_loop.py"
SPEC = importlib.util.spec_from_file_location("grounded_loop", MODULE_PATH)
assert SPEC and SPEC.loader
grounded = importlib.util.module_from_spec(SPEC)
sys.modules["grounded_loop"] = grounded
SPEC.loader.exec_module(grounded)


SNAPSHOT = {
    "requested_ward": "Emergency",
    "wards": [{"ward": "Emergency", "total_beds": 3, "occupied": 1, "available": 2,
               "reserved": 0, "maintenance": 0, "monitored_beds": 3,
               "occupancy_pct": 33.3, "care_categories": ["Short-term"]}],
}


class FakeProvider:
    def __init__(self, payload=None, ok=True, raises=None):
        self.payload = payload if payload is not None else {"ok": True, "data": SNAPSHOT}
        self.ok = ok
        self.raises = raises
        self.calls = []

    async def list_tools(self):
        return [
            grounded.ToolSpec("homs_ward_occupancy_status", "ward snapshot",
                              {"type": "object", "properties": {"ward": {"type": "string"}}}),
            grounded.ToolSpec("homs_echo", "echo", {"type": "object"}),
        ]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.raises:
            raise self.raises
        return grounded.ToolOutcome(ok=self.ok, payload=self.payload)


class ScriptedChat:
    """Replays assistant messages and records what the loop sent."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.requests = []

    def __call__(self, messages, tools):
        self.requests.append({"messages": list(messages), "tools": tools})
        if not self.messages:
            raise RuntimeError("script exhausted")
        item = self.messages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def tool_call(name, arguments):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def final(answer, cited):
    return {"role": "assistant",
            "content": json.dumps({"answer": answer, "cited_calls": cited, "limitations": "snapshot"})}


def run(loop, question="How many beds are free in Emergency?"):
    return asyncio.run(loop.run(question))


def make_loop(chat, provider=None, **kwargs):
    return grounded.GroundedAgenticLoop(chat=chat, provider=provider or FakeProvider(),
                                        log=lambda _: None, **kwargs)


def test_tool_then_grounded_answer():
    chat = ScriptedChat(
        tool_call("homs_ward_occupancy_status", {"ward": "Emergency"}),
        final("Emergency has 2 of 3 beds available (33.3% occupied).", ["call-1"]),
    )
    provider = FakeProvider()
    result = run(make_loop(chat, provider))

    assert provider.calls == [("homs_ward_occupancy_status", {"ward": "Emergency"})]
    assert result.stop_reason == "answered"
    assert result.grounding["status"] == "grounded"
    assert chat.requests[0]["tools"][0]["function"]["name"] == "homs_ward_occupancy_status"
    tool_message = chat.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert json.loads(tool_message["content"])["call_id"] == "call-1"


def test_hallucinated_number_is_flagged_partial():
    chat = ScriptedChat(
        tool_call("homs_ward_occupancy_status", {"ward": "Emergency"}),
        final("Emergency has 7 beds available.", ["call-1"]),
    )
    result = run(make_loop(chat))
    assert result.grounding["status"] == "partial"
    assert result.grounding["unsupported_numbers"] == ["7"]


def test_answer_without_tools_is_ungrounded():
    chat = ScriptedChat(final("Emergency has 2 free beds.", []))
    result = run(make_loop(chat))
    assert result.calls == []
    assert result.grounding["status"] == "ungrounded"


def test_citation_to_unknown_call_is_invalid():
    chat = ScriptedChat(
        tool_call("homs_ward_occupancy_status", {}),
        final("Emergency has 2 available.", ["call-1", "call-9"]),
    )
    result = run(make_loop(chat))
    assert result.grounding["invalid_citations"] == ["call-9"]
    assert result.grounding["status"] == "partial"


def test_unknown_tool_is_rejected_without_execution():
    chat = ScriptedChat(tool_call("drop_database", {}), final("Could not answer.", []))
    provider = FakeProvider()
    result = run(make_loop(chat, provider))
    assert provider.calls == []
    assert result.calls[0].status == "rejected"
    assert "not available" in result.calls[0].error


def test_allowlist_hides_other_tools():
    chat = ScriptedChat(tool_call("homs_echo", {"message": "hi"}), final("n/a", []))
    provider = FakeProvider()
    result = run(make_loop(chat, provider, allowed_tools=["homs_ward_occupancy_status"]))
    assert result.tools == ["homs_ward_occupancy_status"]
    assert provider.calls == []


def test_string_arguments_are_parsed_and_bad_json_rejected():
    assert grounded.parse_tool_arguments('{"ward": "Emergency"}') == {"ward": "Emergency"}
    assert grounded.parse_tool_arguments(None) == {}
    with pytest.raises(ValueError):
        grounded.parse_tool_arguments("{not json")
    with pytest.raises(ValueError):
        grounded.parse_tool_arguments("[1, 2]")


def test_tool_error_is_passed_to_model_not_raised():
    provider = FakeProvider(payload={"ok": False, "error": {"code": "upstream_unavailable"}}, ok=False)
    chat = ScriptedChat(tool_call("homs_ward_occupancy_status", {}),
                        final("Occupancy service is unavailable.", ["call-1"]))
    result = run(make_loop(chat, provider))
    assert result.calls[0].ok is False
    assert result.grounding["status"] == "ungrounded"


def test_transport_exception_is_recorded_as_failed():
    provider = FakeProvider(raises=ConnectionError("refused"))
    chat = ScriptedChat(tool_call("homs_ward_occupancy_status", {}), final("Unavailable.", []))
    result = run(make_loop(chat, provider))
    assert result.calls[0].status == "failed"
    assert "ConnectionError" in result.calls[0].error


def test_step_budget_forces_final_answer_without_tools():
    chat = ScriptedChat(
        tool_call("homs_ward_occupancy_status", {}),
        tool_call("homs_ward_occupancy_status", {}),
        final("Emergency has 2 available.", ["call-2"]),
    )
    result = run(make_loop(chat, max_steps=2))
    assert result.stop_reason == "step_budget_exhausted"
    assert chat.requests[-1]["tools"] is None
    assert result.grounding["status"] == "grounded"


def test_tool_call_budget_withdraws_tools():
    chat = ScriptedChat(
        tool_call("homs_ward_occupancy_status", {}),
        final("Emergency has 2 available.", ["call-1"]),
    )
    run(make_loop(chat, max_tool_calls=1))
    assert chat.requests[1]["tools"] is None


def test_model_unavailable_stops_cleanly():
    chat = ScriptedChat(RuntimeError("Ollama request failed: refused"))
    result = run(make_loop(chat))
    assert result.stop_reason == "model_unavailable"
    assert result.final is None
    assert result.grounding["status"] == "ungrounded"


@pytest.mark.parametrize("question", ["", "   ", "x" * 1001])
def test_question_is_validated(question):
    with pytest.raises(ValueError):
        run(make_loop(ScriptedChat()), question)


def test_final_answer_parsing_tolerates_fences_and_prose():
    parsed = grounded.parse_final_answer('```json\n{"answer": "ok", "cited_calls": ["call-1"]}\n```')
    assert parsed["cited_calls"] == ["call-1"]
    prose = grounded.parse_final_answer("There are 2 beds.")
    assert prose == {"answer": "There are 2 beds.", "cited_calls": [], "limitations": "unstructured"}


def test_evidence_files_are_written(tmp_path):
    chat = ScriptedChat(tool_call("homs_ward_occupancy_status", {"ward": "Emergency"}),
                        final("Emergency has 2 available.", ["call-1"]))
    result = run(make_loop(chat))
    record = grounded.build_record(result, run_id="RUN", component="student-4",
                                   model="llama3.1:8b", ollama_url="u", mcp_url="m")
    paths = grounded.save_evidence(record, tmp_path)
    saved = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert saved["release"] == "R1"
    assert saved["tool_calls"][0]["call_id"] == "call-1"
    assert "Grounding: **grounded**" in paths["markdown"].read_text(encoding="utf-8")
