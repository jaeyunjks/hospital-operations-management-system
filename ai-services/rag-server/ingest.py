#!/usr/bin/env python3
"""Build the shared HOMS RAG index from ``knowledge/``.

Run from the repository root after adding or editing knowledge documents::

    python3 ai-services/rag-server/ingest.py

The running server picks up the rebuilt index on its next request.
"""

from __future__ import annotations

import sys
import time
from collections import Counter

from rag import OllamaClient, RagError, build_index, load_settings


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    print(f"Knowledge: {settings.knowledge_dir}")
    print(f"Embedding model: {settings.embed_model} via {settings.ollama_url}")
    started = time.perf_counter()
    try:
        index = build_index(settings, OllamaClient(settings.ollama_url, settings.timeout), log=print)
    except RagError as error:
        print(f"Ingest failed [{error.code}]: {error.message}", file=sys.stderr)
        return 1
    per_feature = Counter(chunk["feature"] for chunk in index["chunks"])
    documents = len({chunk["source"] for chunk in index["chunks"]})
    print(f"Indexed {len(index['chunks'])} chunks from {documents} documents "
          f"in {time.perf_counter() - started:.1f}s -> {settings.index_path}")
    for feature, count in sorted(per_feature.items()):
        print(f"  {feature}: {count} chunk(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
