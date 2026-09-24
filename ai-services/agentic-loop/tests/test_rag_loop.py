from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

AGENT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT_DIR))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rag_loop = _load("shared_rag_loop", "rag_loop.py")
agentic_loop = _load("shared_agentic_loop_modes", "agentic_loop.py")

DOCUMENTS = [
    {"source": "student-3/expiry.md", "feature": "student-3", "title": "Batch Expiry",
     "sections": ["Expiry categories", "Writing off a batch"], "chunks": 2},
    {"source": "student-3/orders.md", "feature": "student-3", "title": "Purchase Orders",
     "sections": ["Introduction", "Lifecycle"], "chunks": 2},
]


def answered(source="student-3/expiry.md", section="Writing off a batch", confidence="high"):
    return {"status": "answered", "confidence": confidence, "reason": None, "answer": "Managers only [S1].",
            "citations": [{"id": "S1", "source": source, "section": section, "score": 0.84, "snippet": "..."}],
            "retrieval": {"top_score": 0.84, "threshold": 0.62, "considered": 4, "relevant": 3},
            "duration_ms": 900}


REFUSED = {"status": "insufficient_context", "confidence": "none", "reason": "no_relevant_context",
           "answer": "not enough information", "citations": [],
           "retrieval": {"top_score": 0.49, "threshold": 0.62, "considered": 4, "relevant": 0}, "duration_ms": 20}


class FakeRag:
    def __init__(self, answers=None, documents=DOCUMENTS):
        self.answers = answers or {}
        self.documents = documents
        self.queries = []

    def sources(self, feature):
        return {"documents": self.documents}

    def query(self, question, feature):
        self.queries.append((question, feature))
        return self.answers.get(question, REFUSED)


class ScriptedModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


PLAN = {"analysis": "Expiry and orders.", "answerable": ["Who can write off a batch?", "What are the PO statuses?"],
        "out_of_scope": "How many ICU beds are free?"}
REVIEW = {"assessment": "All good.", "retry": [], "next_action": "None.", "outcome": "pass"}


def run(rag, model, **kwargs):
    loop = rag_loop.RagValidationLoop(rag=rag, generate=model, component="student-3", log=lambda _m: None,
                                      questions=kwargs.pop("questions", 2), **kwargs)
    return loop.run()


def test_all_cases_pass_with_model_plan_and_control_question():
    rag = FakeRag({"Who can write off a batch?": answered(),
                   "What are the PO statuses?": answered("shared/overview.md", "Ports", "low")})
    result = run(rag, ScriptedModel(PLAN, REVIEW))
    assert result["summary"]["verdict"] == "pass"
    assert [(c["id"], c["kind"], c["origin"]) for c in result["cases"]] == [
        ("Q1", "answerable", "model"), ("Q2", "answerable", "model"),
        ("Q3", "out_of_scope", "model"), ("Q4", "out_of_scope", "control")]
    assert result["cases"][-1]["question"] == rag_loop.CONTROL_QUESTION
    assert all(feature == "student-3" for _q, feature in rag.queries)
    assert len(result["rounds"]) == 1


def test_plan_prompt_lists_only_titles_and_sections():
    model = ScriptedModel(PLAN, REVIEW)
    run(FakeRag(), model)
    assert '"Writing off a batch"' in model.prompts[0]
    assert "Managers only" not in model.prompts[0]


@pytest.mark.parametrize("observation,reason", [
    (REFUSED, "expected answered, got insufficient_context"),
    ({**answered(), "citations": []}, "no citations"),
    (answered("student-4/beds.md", "Wards"), "cited another feature's knowledge: student-4/beds.md"),
    ({**answered(), "confidence": "none"}, "invalid confidence"),
    ({"error": {"code": "ollama_unavailable"}}, "RAG server error: ollama_unavailable"),
])
def test_answerable_checks(observation, reason):
    check = rag_loop.check_case("answerable", rag_loop.observe(observation), "student-3")
    assert check["passed"] is False
    assert any(reason in item for item in check["reasons"])


def test_out_of_scope_must_be_refused_without_citations():
    assert rag_loop.check_case("out_of_scope", rag_loop.observe(REFUSED), "student-3")["passed"] is True
    check = rag_loop.check_case("out_of_scope", rag_loop.observe(answered()), "student-3")
    assert check["reasons"] == ["expected insufficient_context, got answered", "refusal carried citations"]


def test_adapt_rephrases_a_failed_question_and_retry_can_pass():
    rag = FakeRag({"Who can write off a batch?": answered(),
                   "What is the purchase order lifecycle?": answered("student-3/orders.md", "Lifecycle")})
    adapt = {"assessment": "Q2 missed.", "outcome": "fail", "next_action": "retry",
             "retry": [{"id": "Q2", "question": "What is the purchase order lifecycle?"},
                       {"id": "Q1", "question": "passed cases are ignored"},
                       {"id": "Q3", "question": "out of scope cases are ignored"}]}
    model = ScriptedModel(PLAN, adapt, REVIEW)
    result = run(rag, model)
    assert len(result["rounds"]) == 2
    assert [r["id"] for r in result["rounds"][1]["results"]] == ["Q2-retry"]
    assert result["summary"]["verdict"] == "pass"
    q2 = next(case for case in result["summary"]["final"] if case["id"] == "Q2")
    assert q2["retried"] is True and q2["question"] == "What is the purchase order lifecycle?"
    assert '"retry_allowed": false' in model.prompts[-1]


def test_at_most_two_rounds_and_failure_is_reported():
    adapt = {"assessment": "x", "outcome": "fail", "next_action": "x",
             "retry": [{"id": "Q1", "question": "Still unanswerable?"}]}
    model = ScriptedModel(PLAN, adapt, adapt)
    result = run(FakeRag(), model, questions=1)
    assert len(result["rounds"]) == 2
    assert result["summary"]["verdict"] == "fail"
    assert result["summary"]["failed"] == ["Q1"]


def test_out_of_scope_answer_fails_the_verdict():
    rag = FakeRag({"Who can write off a batch?": answered(), "What are the PO statuses?": answered(),
                   rag_loop.CONTROL_QUESTION: answered()})
    result = run(rag, ScriptedModel(PLAN, REVIEW, REVIEW))
    assert result["summary"]["verdict"] == "fail"
    assert result["summary"]["failed"] == ["Q4"]


def test_model_unavailable_falls_back_to_section_questions():
    rag = FakeRag({"What does the Batch Expiry documentation say about expiry categories?": answered(),
                   "What does the Purchase Orders documentation say about lifecycle?": answered()})
    result = run(rag, ScriptedModel(RuntimeError("down"), RuntimeError("down")))
    assert result["plan_error"] == "down"
    assert [c["origin"] for c in result["cases"]] == ["fallback", "fallback", "control"]
    assert result["summary"]["verdict"] == "pass"
    assert result["rounds"][0]["adapt"]["error"] == "down"


def test_user_questions_skip_generation():
    model = ScriptedModel(REVIEW)
    result = run(FakeRag({"Who can write off a batch?": answered()}), model,
                 user_questions=["Who can write off a batch?"])
    assert [c["origin"] for c in result["cases"]] == ["user", "control"]
    assert len(model.prompts) == 1  # only ADAPT; PLAN generation skipped


def test_feature_without_documents_fails():
    result = run(FakeRag(documents=[]), ScriptedModel(REVIEW))
    assert result["summary"]["verdict"] == "fail"
    assert "no answerable cases" in result["summary"]["notes"][0]


def test_invalid_generated_questions_are_replaced():
    plan = {"answerable": ["", 42, "x" * 400], "out_of_scope": None}
    result = run(FakeRag(), ScriptedModel(plan, REVIEW))
    assert [c["origin"] for c in result["cases"]] == ["fallback", "fallback", "control"]


def test_main_writes_evidence_and_returns_exit_codes(tmp_path, monkeypatch):
    rag = FakeRag({"Who can write off a batch?": answered()})
    monkeypatch.setattr(rag_loop, "RagClient", lambda url, timeout: rag)
    monkeypatch.setattr(rag_loop, "ollama_generate_json", lambda prompt, **_kw: REVIEW)
    code = rag_loop.main(["--student", "3", "--question", "Who can write off a batch?",
                          "--logs-dir", str(tmp_path)])
    assert code == 0
    record = json.loads(next(tmp_path.glob("*-rag-validation.json")).read_text())
    assert record["mode"] == "rag" and record["component"] == "student-3"
    markdown = next(tmp_path.glob("*-rag-validation.md")).read_text()
    assert "Verdict: **PASS** (2/2 cases passed)" in markdown
    assert "| Q1 | answerable | Who can write off a batch? | answered | high |" in markdown


def test_main_reports_unreachable_rag_server(tmp_path):
    code = rag_loop.main(["--student", "3", "--rag-url", "http://127.0.0.1:9", "--logs-dir", str(tmp_path)])
    assert code == 3
    assert list(tmp_path.iterdir()) == []


def test_agentic_loop_dispatches_modes(monkeypatch):
    calls = []
    fake = type("Mode", (), {"main": staticmethod(lambda argv: calls.append(argv) or 7)})
    monkeypatch.setattr(agentic_loop.importlib, "import_module", lambda name: calls.append(name) or fake)
    assert agentic_loop.main(["--mode", "rag", "--student", "3"]) == 7
    assert agentic_loop.main(["--student", "3", "--mode", "mcp", "--question", "q"]) == 7
    assert calls == ["rag_loop", ["--student", "3"], "grounded_loop", ["--student", "3", "--question", "q"]]


def test_command_mode_remains_the_default():
    assert agentic_loop.select_mode(["--student", "3", "--command", "pytest"]) == (
        "command", ["--student", "3", "--command", "pytest"])
