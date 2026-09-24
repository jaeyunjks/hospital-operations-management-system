from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest

import rag


def test_chunk_markdown_keeps_title_and_section_and_splits_long_sections():
    long_section = "\n\n".join(f"Paragraph {n} " + "x" * 500 for n in range(4))
    chunks = rag.chunk_markdown(f"# Title\n\nIntro.\n\n## First\n\nBody.\n\n### Deep\n\n{long_section}\n", "s/doc.md")
    assert [(c["title"], c["section"]) for c in chunks[:3]] == [
        ("Title", "Introduction"), ("Title", "First"), ("Title", "Deep")]
    assert len([c for c in chunks if c["section"] == "Deep"]) == 2
    assert all(len(c["text"]) <= rag.CHUNK_TARGET_CHARS for c in chunks)


def test_title_falls_back_to_filename():
    assert rag.chunk_markdown("## Only\n\nBody.", "shared/staff-rules.md")[0]["title"] == "Staff Rules"


def test_only_feature_folders_are_ingested_and_readmes_are_skipped(knowledge_dir):
    sources = [source for _feature, source, _path in rag.knowledge_documents(knowledge_dir)]
    assert sources == ["shared/overview.md", "student-3/stock.md", "student-4/beds.md"]


def test_build_index_writes_normalised_embeddings_with_document_prefix(settings, fake):
    index = rag.build_index(settings, fake)
    stored = json.loads(settings.index_path.read_text())
    assert stored["embed_model"] == "nomic-embed-text"
    assert stored["fingerprint"] == rag.knowledge_fingerprint(settings.knowledge_dir)
    assert [c["id"] for c in index["chunks"]] == [
        "shared/overview.md#1", "student-3/stock.md#1", "student-3/stock.md#2",
        "student-3/stock.md#3", "student-4/beds.md#1"]
    for chunk in stored["chunks"]:
        assert abs(sum(v * v for v in chunk["embedding"]) - 1) < 1e-4
    assert all(text.startswith("search_document: ") for text in fake.embedded)


def test_empty_knowledge_base_is_rejected(settings, fake, tmp_path):
    with pytest.raises(rag.RagError) as error:
        rag.build_index(replace(settings, knowledge_dir=tmp_path / "empty"), fake)
    assert error.value.code == "knowledge_empty"


def test_retrieve_is_scoped_to_feature_plus_shared(engine, fake):
    result = engine.retrieve("stock reorder", feature="student-3", top_k=8)
    features = {r["feature"] for r in result["results"]}
    assert features == {"student-3", "shared"}
    assert result["results"][0]["section"] == "Low stock"
    assert result["results"][0]["relevant"] is True
    assert [r["score"] for r in result["results"]] == sorted((r["score"] for r in result["results"]), reverse=True)
    assert fake.embedded == ["search_query: stock reorder"]
    assert fake.chats == []


def test_retrieve_without_feature_searches_every_feature(engine):
    features = {r["feature"] for r in engine.retrieve("bed", top_k=8)["results"]}
    assert features == {"shared", "student-3", "student-4"}


def test_grounded_answer_has_citations_and_confidence(engine, fake):
    result = engine.query("stock reorder", feature="student-3")
    assert result["status"] == "answered"
    assert result["reason"] is None
    assert result["answer"] == "Stock is low at or below its reorder level [S1]."
    assert result["citations"][0]["id"] == "S1"
    assert result["citations"][0]["source"] == "student-3/stock.md"
    assert result["citations"][0]["section"] == "Low stock"
    assert result["retrieval"]["relevant"] == 2  # "Low stock" plus the Stock introduction
    assert result["confidence"] == "high"  # cited score 1.0 with corroborating evidence
    system, user = fake.chats[0]
    assert "ONLY the numbered sources" in system["content"]
    assert "[S1] Stock - Low stock (student-3/stock.md)" in user["content"]
    assert user["content"].endswith("Question: stock reorder")


def test_no_relevant_context_never_calls_the_model(engine, fake):
    result = engine.query("weather", feature="student-3")
    assert result["status"] == "insufficient_context"
    assert result["reason"] == "no_relevant_context"
    assert result["confidence"] == "none"
    assert result["citations"] == []
    assert result["answer"] == rag.INSUFFICIENT_MESSAGE
    assert fake.chats == []


def test_other_features_documents_do_not_answer(engine, fake):
    assert engine.query("bed", feature="student-3")["status"] == "insufficient_context"
    assert engine.query("bed", feature="student-4")["status"] == "answered"


@pytest.mark.parametrize("reply,reason", [
    ("INSUFFICIENT_CONTEXT", "model_declined"),
    ("Insufficient context.", "model_declined"),
    ("", "model_declined"),
    ("Stock is low at the reorder level.", "uncited_answer"),
    ("Stock is low [S7].", "uncited_answer"),
])
def test_unsupported_model_replies_become_insufficient_context(engine, fake, reply, reason):
    fake.reply = reply
    result = engine.query("stock reorder", feature="student-3")
    assert (result["status"], result["reason"], result["citations"]) == ("insufficient_context", reason, [])


def test_invalid_citations_are_removed_and_lower_confidence(engine, fake):
    fake.reply = "Stock is low [S1][S7]. Reorder then [S9]."
    result = engine.query("stock reorder", feature="student-3")
    assert result["status"] == "answered"
    assert result["reason"] == "invalid_citations_removed"
    assert result["answer"] == "Stock is low [S1]. Reorder then."
    assert [c["id"] for c in result["citations"]] == ["S1"]
    assert result["confidence"] == "medium"  # high, lowered one level for invalid citations


def test_extract_citations_handles_grouped_and_repeated_ids():
    assert rag.extract_citations("A [S1, S3]. B [S2][S1]. C (S9) [see S04]") == ["S1", "S3", "S2", "S4"]


@pytest.mark.parametrize("scores,relevant,invalid,expected", [
    ([0.95], 2, 0, "high"),
    ([0.95], 1, 0, "medium"),
    ([0.72, 0.6], 3, 0, "medium"),
    ([0.6], 3, 0, "low"),
    ([0.95], 2, 1, "medium"),
    ([0.6], 3, 2, "low"),
])
def test_confidence_category(settings, scores, relevant, invalid, expected):
    assert rag.confidence_category(scores, relevant, invalid, settings) == expected


@pytest.mark.parametrize("question,feature,top_k", [
    ("", None, None), ("   ", None, None), (42, None, None), ("x" * 501, None, None),
    ("ok", "student-9", None), ("ok", "shared", None),
    ("ok", None, 0), ("ok", None, 9), ("ok", None, True), ("ok", None, "3"),
])
def test_invalid_requests_are_rejected_before_embedding(engine, fake, question, feature, top_k):
    with pytest.raises(rag.RagError) as error:
        engine.query(question, feature, top_k)
    assert (error.value.code, error.value.status) == ("validation_error", 400)
    assert fake.embedded == []


def test_missing_index_is_reported(settings, fake):
    with pytest.raises(rag.RagError) as error:
        rag.RagEngine(settings, fake).query("stock")
    assert error.value.code == "index_missing"
    assert rag.RagEngine(settings, fake).index_status()["ready"] is False


def test_index_built_with_another_model_is_refused(engine, settings, fake):
    engine = rag.RagEngine(replace(settings, embed_model="other-embedder"), fake)
    with pytest.raises(rag.RagError) as error:
        engine.index()
    assert error.value.code == "index_model_mismatch"


def test_stale_index_is_detected_and_rebuilt_index_is_reloaded(engine, settings, fake):
    assert engine.index_status()["stale"] is False
    (settings.knowledge_dir / "student-4" / "theatres.md").write_text("# Theatres\n\n## Rules\n\nweather.\n")
    assert engine.index_status()["stale"] is True
    rag.build_index(settings, fake)
    os.utime(settings.index_path, (1, 1))  # force a different mtime on fast filesystems
    assert engine.index_status()["stale"] is False
    assert engine.index_status()["chunks"] == 6


def test_sources_lists_documents_with_sections(engine):
    documents = engine.sources("student-3")
    assert documents == [{"source": "student-3/stock.md", "feature": "student-3", "title": "Stock",
                          "sections": ["Introduction", "Low stock", "Expiry"], "chunks": 3}]
    assert len(engine.sources()) == 3


def test_settings_validation():
    defaults = rag.load_settings({})
    assert (defaults.min_score, defaults.medium_score, defaults.high_score) == (0.62, 0.70, 0.80)
    assert (defaults.embed_model, defaults.chat_model, defaults.top_k) == ("nomic-embed-text", "llama3.2:3b", 4)
    for env in ({"HOMS_RAG_MIN_SCORE": "0.9"}, {"HOMS_RAG_MIN_SCORE": "nan"},
                {"HOMS_RAG_TOP_K": "0"}, {"HOMS_RAG_TOP_K": "x"}, {"HOMS_RAG_TIMEOUT": "0"}):
        with pytest.raises(ValueError):
            rag.load_settings(env)
