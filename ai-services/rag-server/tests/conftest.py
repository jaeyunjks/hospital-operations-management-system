from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rag  # noqa: E402

VOCABULARY = ("stock", "reorder", "expiry", "batch", "port", "bed", "weather")

KNOWLEDGE = {
    "shared/overview.md": "# Overview\n\n## Ports\n\nEach service has a port.\n",
    "student-3/stock.md": (
        "# Stock\n\nIntro text.\n\n## Low stock\n\nstock at or below reorder level.\n\n"
        "## Expiry\n\nexpiry of each batch.\n"
    ),
    "student-3/README.md": "# Instructions\n\nstock reorder expiry batch\n",
    "student-4/beds.md": "# Beds\n\n## Wards\n\nbed allocation.\n",
    "notes/ignored.md": "# Ignored\n\nstock reorder\n",
}


class FakeOllama:
    """Deterministic keyword embeddings and scripted chat replies."""

    def __init__(self):
        self.embedded = []
        self.chats = []
        self.reply = "Stock is low at or below its reorder level [S1]."
        self.available = ["nomic-embed-text:latest", "llama3.2:3b"]
        self.error = None

    def embed(self, model, texts):
        if self.error:
            raise self.error
        self.embedded.extend(texts)
        return [[1.0 if word in text.lower() else 0.0 for word in VOCABULARY] + [0.1] for text in texts]

    def chat(self, model, messages):
        if self.error:
            raise self.error
        self.chats.append(messages)
        return self.reply

    def models(self):
        if self.error:
            raise self.error
        return self.available


@pytest.fixture
def knowledge_dir(tmp_path):
    root = tmp_path / "knowledge"
    for relative, text in KNOWLEDGE.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def settings(tmp_path, knowledge_dir):
    base = rag.load_settings({})
    return replace(base, knowledge_dir=knowledge_dir, index_path=tmp_path / "index" / "index.json",
                   min_score=0.5, medium_score=0.7, high_score=0.9)


@pytest.fixture
def fake():
    return FakeOllama()


@pytest.fixture
def engine(settings, fake):
    rag.build_index(settings, fake)
    fake.embedded.clear()
    return rag.RagEngine(settings, fake)
