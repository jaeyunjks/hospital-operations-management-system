from __future__ import annotations

import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "agentic_loop.py"
SPEC = importlib.util.spec_from_file_location("shared_agentic_loop", MODULE_PATH)
assert SPEC and SPEC.loader
agentic_loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agentic_loop)


def test_model_default_is_release_zero_model():
    assert agentic_loop.DEFAULT_MODEL == "llama3.1:8b"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("5", "student-5"), ("student-2", "student-2"), ("STUDENT-10", "student-10")],
)
def test_normalise_student(value, expected):
    assert agentic_loop.normalise_student(value) == expected


def test_normalise_student_rejects_paths():
    with pytest.raises(ValueError):
        agentic_loop.normalise_student("../student-5")


def test_validation_captures_real_output(tmp_path):
    command = (
        f"{shlex.quote(sys.executable)} -c "
        + shlex.quote("import sys; print('checked'); print('note', file=sys.stderr)")
    )
    observed = agentic_loop.run_validation(command, cwd=tmp_path, timeout=5)

    assert observed["return_code"] == 0
    assert observed["stdout"] == "checked\n"
    assert observed["stderr"] == "note\n"
    assert observed["cwd"] == str(tmp_path)


def test_evidence_is_saved_as_timestamped_json_and_markdown(tmp_path):
    record = {
        "schema_version": 1,
        "release": "R0",
        "run_id": "20260831T010203.000000Z",
        "component": "student-5",
        "task": "focused test",
        "model": "llama3.1:8b",
        "ollama_url": "http://127.0.0.1:11434",
        "file_inventory": [],
        "plan": {"analysis": "validate", "validation_steps": ["run tests"]},
        "plan_error": None,
        "observe": {
            "command": "pytest -q",
            "argv": ["pytest", "-q"],
            "cwd": "/repo/student-5",
            "started_at": "2026-08-31T01:02:03.000Z",
            "finished_at": "2026-08-31T01:02:04.000Z",
            "duration_seconds": 1.0,
            "return_code": 0,
            "timed_out": False,
            "stdout": "1 passed\n",
            "stderr": "",
        },
        "adapt": {
            "assessment": "Validation passed.",
            "next_action": "Record the evidence.",
            "outcome": "pass",
        },
        "adapt_error": None,
    }

    paths = agentic_loop.save_evidence(record, tmp_path)

    assert paths["json"].name == "20260831T010203.000000Z-agentic-loop.json"
    assert paths["markdown"].name == "20260831T010203.000000Z-agentic-loop.md"
    assert json.loads(paths["json"].read_text())["observe"]["stdout"] == "1 passed\n"
    assert "## PLAN" in paths["markdown"].read_text()
    assert "## ADAPT" in paths["markdown"].read_text()
