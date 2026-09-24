"""Retrieval, grounding and confidence logic for the shared HOMS RAG server.

The engine never trusts the model for facts it can check itself: relevance
comes from embedding similarity, the insufficient-context decision is made
before the model is called, citations are validated against the sources that
were actually supplied, and the confidence category is computed from the
retrieval scores of the cited sources.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent
DEFAULT_KNOWLEDGE_DIR = ROOT / "knowledge"
DEFAULT_INDEX_PATH = ROOT / ".index" / "index.json"
PROMPT_PATH = ROOT / "prompts" / "grounded_answer_v1.md"

SCHEMA_VERSION = "1.0"
FEATURES = ("student-1", "student-2", "student-3", "student-4", "student-5")
SHARED = "shared"
MAX_QUESTION_LENGTH = 500
MAX_TOP_K = 8
SNIPPET_LENGTH = 280
CHUNK_TARGET_CHARS = 1200
EMBED_BATCH_SIZE = 16
INSUFFICIENT_MARKER = "INSUFFICIENT_CONTEXT"
INSUFFICIENT_MESSAGE = (
    "The knowledge base does not contain enough relevant information to answer "
    "this question, so no answer was generated."
)
CONFIDENCE_LEVELS = ("low", "medium", "high")


class RagError(Exception):
    """A failure the HTTP layer can report as a stable JSON error."""

    def __init__(self, code: str, message: str, status: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Settings:
    ollama_url: str
    embed_model: str
    chat_model: str
    timeout: float
    knowledge_dir: Path
    index_path: Path
    min_score: float
    medium_score: float
    high_score: float
    top_k: int


def _float_env(env, name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(env.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def load_settings(env=None) -> Settings:
    """Read and validate engine settings from the environment."""

    env = os.environ if env is None else env
    min_score = _float_env(env, "HOMS_RAG_MIN_SCORE", 0.62, 0.0, 1.0)
    medium_score = _float_env(env, "HOMS_RAG_MEDIUM_SCORE", 0.70, 0.0, 1.0)
    high_score = _float_env(env, "HOMS_RAG_HIGH_SCORE", 0.80, 0.0, 1.0)
    if not min_score <= medium_score <= high_score:
        raise ValueError(
            "Scores must satisfy HOMS_RAG_MIN_SCORE <= HOMS_RAG_MEDIUM_SCORE <= HOMS_RAG_HIGH_SCORE"
        )
    try:
        top_k = int(env.get("HOMS_RAG_TOP_K", "4"))
    except ValueError as error:
        raise ValueError("HOMS_RAG_TOP_K must be an integer") from error
    if not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"HOMS_RAG_TOP_K must be between 1 and {MAX_TOP_K}")
    return Settings(
        ollama_url=env.get("OLLAMA_URL", "http://127.0.0.1:11434").strip().rstrip("/"),
        embed_model=env.get("HOMS_RAG_EMBED_MODEL", "nomic-embed-text").strip(),
        chat_model=env.get("HOMS_RAG_CHAT_MODEL", "llama3.2:3b").strip(),
        timeout=_float_env(env, "HOMS_RAG_TIMEOUT", 90.0, 1.0, 600.0),
        knowledge_dir=Path(env.get("HOMS_RAG_KNOWLEDGE_DIR", str(DEFAULT_KNOWLEDGE_DIR))),
        index_path=Path(env.get("HOMS_RAG_INDEX_PATH", str(DEFAULT_INDEX_PATH))),
        min_score=min_score,
        medium_score=medium_score,
        high_score=high_score,
        top_k=top_k,
    )


# --------------------------------------------------------------------------- #
# Ollama client
# --------------------------------------------------------------------------- #


class OllamaClient:
    """Minimal blocking client for the local Ollama embedding and chat APIs."""

    def __init__(self, base_url: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise RagError(
                    "model_unavailable",
                    f"Ollama model '{payload.get('model')}' is not installed; run: ollama pull {payload.get('model')}",
                ) from error
            raise RagError("ollama_error", f"Ollama returned HTTP {error.code}") from error
        except (socket.timeout, TimeoutError) as error:
            raise RagError("ollama_timeout", "Ollama did not respond in time", 504) from error
        except urllib.error.URLError as error:
            if isinstance(getattr(error, "reason", None), (socket.timeout, TimeoutError)):
                raise RagError("ollama_timeout", "Ollama did not respond in time", 504) from error
            raise RagError("ollama_unavailable", "Ollama is not reachable") from error
        except (json.JSONDecodeError, OSError) as error:
            raise RagError("ollama_unavailable", "Ollama returned an invalid response") from error

    def embed(self, model: str, texts: Sequence[str]) -> List[List[float]]:
        body = self._post("/api/embed", {"model": model, "input": list(texts)})
        vectors = body.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise RagError("ollama_error", "Ollama returned an unexpected embedding response")
        return vectors

    def chat(self, model: str, messages: List[Dict[str, str]]) -> str:
        body = self._post(
            "/api/chat",
            {"model": model, "messages": messages, "stream": False, "options": {"temperature": 0}},
        )
        content = (body.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise RagError("ollama_error", "Ollama returned an unexpected chat response")
        return content

    def models(self) -> List[str]:
        request = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
        with urllib.request.urlopen(request, timeout=min(self.timeout, 3)) as response:
            body = json.loads(response.read().decode("utf-8"))
        return [model.get("name", "") for model in body.get("models", [])]


# --------------------------------------------------------------------------- #
# Knowledge loading and chunking
# --------------------------------------------------------------------------- #

_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


def _split_long(body: str, limit: int) -> List[str]:
    """Split one section on paragraph boundaries into chunks of about ``limit``."""

    parts, current = [], ""
    for paragraph in [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]:
        if current and len(current) + len(paragraph) + 2 > limit:
            parts.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts


def chunk_markdown(text: str, source: str) -> List[Dict[str, str]]:
    """Split one markdown document into heading-scoped chunks.

    Each chunk keeps its document title and section heading so a citation can
    point a reader to the exact place the answer came from.
    """

    title = Path(source).stem.replace("-", " ").title()
    sections: List[tuple[str, List[str]]] = []
    heading = "Introduction"
    lines: List[str] = []
    for line in text.splitlines():
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 1:
            title = match.group(2)
            continue
        if match:
            if lines:
                sections.append((heading, lines))
            heading, lines = match.group(2), []
            continue
        lines.append(line)
    if lines:
        sections.append((heading, lines))

    chunks = []
    for heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        for part in _split_long(body, CHUNK_TARGET_CHARS):
            chunks.append({"title": title, "section": heading, "text": part})
    return chunks


def knowledge_documents(knowledge_dir: Path) -> List[tuple[str, str, Path]]:
    """Return (feature, relative source, path) for every ingestible document.

    Only ``shared/`` and ``student-1`` .. ``student-5`` folders are read, and
    each folder's README.md (contributor instructions) is skipped.
    """

    documents = []
    for feature in (SHARED, *FEATURES):
        folder = knowledge_dir / feature
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            documents.append((feature, f"{feature}/{path.name}", path))
    return documents


def knowledge_fingerprint(knowledge_dir: Path) -> str:
    digest = hashlib.sha256()
    for _feature, source, path in knowledge_documents(knowledge_dir):
        digest.update(source.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _embedding_text(model: str, text: str, kind: str) -> str:
    # nomic-embed-text is trained with task prefixes for asymmetric retrieval.
    if "nomic" in model:
        return f"search_{kind}: {text}"
    return text


def _normalise(vector: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm or not math.isfinite(norm):
        raise RagError("ollama_error", "Ollama returned an empty embedding")
    return [round(value / norm, 6) for value in vector]


def build_index(settings: Settings, client: OllamaClient,
                log: Callable[[str], None] = lambda _message: None) -> Dict[str, Any]:
    """Chunk and embed the knowledge base, then write the index atomically."""

    chunks = []
    for feature, source, path in knowledge_documents(settings.knowledge_dir):
        document_chunks = chunk_markdown(path.read_text(encoding="utf-8"), source)
        for number, chunk in enumerate(document_chunks, start=1):
            chunks.append({"id": f"{source}#{number}", "feature": feature, "source": source, **chunk})
        log(f"  {source}: {len(document_chunks)} chunk(s)")
    if not chunks:
        raise RagError("knowledge_empty", f"No knowledge documents found in {settings.knowledge_dir}", 400)

    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start:start + EMBED_BATCH_SIZE]
        vectors = client.embed(
            settings.embed_model,
            [_embedding_text(settings.embed_model, f"{c['title']} - {c['section']}\n{c['text']}", "document")
             for c in batch],
        )
        for chunk, vector in zip(batch, vectors):
            chunk["embedding"] = _normalise(vector)

    index = {
        "schema_version": SCHEMA_VERSION,
        "embed_model": settings.embed_model,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fingerprint": knowledge_fingerprint(settings.knowledge_dir),
        "chunks": chunks,
    }
    settings.index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.index_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(index), encoding="utf-8")
    temporary.replace(settings.index_path)
    return index


# --------------------------------------------------------------------------- #
# Retrieval and grounded answering
# --------------------------------------------------------------------------- #

_CITATION_GROUP = re.compile(r"\[([^\[\]]{1,40})\]")
_SOURCE_ID = re.compile(r"\bS(\d{1,2})\b")


def extract_citations(answer: str) -> List[str]:
    """Return source ids cited in square brackets, in first-use order."""

    cited: List[str] = []
    for group in _CITATION_GROUP.findall(answer):
        for number in _SOURCE_ID.findall(group):
            label = f"S{int(number)}"
            if label not in cited:
                cited.append(label)
    return cited


def _strip_citations(answer: str, invalid: Iterable[str]) -> str:
    invalid = set(invalid)
    if not invalid:
        return answer.strip()

    def replace(match: re.Match) -> str:
        kept = [f"S{int(n)}" for n in _SOURCE_ID.findall(match.group(1)) if f"S{int(n)}" not in invalid]
        if not _SOURCE_ID.search(match.group(1)):
            return match.group(0)
        return f"[{', '.join(kept)}]" if kept else ""

    return re.sub(r"\s+([.,;:])", r"\1", _CITATION_GROUP.sub(replace, answer)).strip()


def confidence_category(cited_scores: Sequence[float], relevant_count: int,
                        invalid_citations: int, settings: Settings) -> str:
    """Map retrieval evidence to high, medium or low confidence.

    high:   the best cited source scores >= high_score and at least two
            retrieved sources are relevant (corroborated evidence);
    medium: the best cited source scores >= medium_score;
    low:    otherwise (still above the relevance threshold).
    Any citation to a source that was not supplied lowers the level by one.
    """

    best = max(cited_scores)
    if best >= settings.high_score and relevant_count >= 2:
        level = 2
    elif best >= settings.medium_score:
        level = 1
    else:
        level = 0
    if invalid_citations:
        level = max(0, level - 1)
    return CONFIDENCE_LEVELS[level]


def _snippet(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= SNIPPET_LENGTH else flat[:SNIPPET_LENGTH - 1].rstrip() + "…"


def validate_request(question: Any, feature: Any, top_k: Any, default_top_k: int) -> tuple[str, Optional[str], int]:
    if not isinstance(question, str) or not question.strip():
        raise RagError("validation_error", "'question' must be a non-blank string", 400)
    if len(question) > MAX_QUESTION_LENGTH:
        raise RagError("validation_error", f"'question' must be at most {MAX_QUESTION_LENGTH} characters", 400)
    if feature is not None and feature not in FEATURES:
        raise RagError("validation_error", f"'feature' must be one of: {', '.join(FEATURES)}", 400)
    if top_k is None:
        top_k = default_top_k
    if type(top_k) is not int or not 1 <= top_k <= MAX_TOP_K:
        raise RagError("validation_error", f"'top_k' must be an integer between 1 and {MAX_TOP_K}", 400)
    return question.strip(), feature, top_k


class RagEngine:
    """Loads the index lazily and answers retrieval and grounded queries."""

    def __init__(self, settings: Settings, client: Optional[OllamaClient] = None):
        self.settings = settings
        self.client = client or OllamaClient(settings.ollama_url, settings.timeout)
        self._index: Optional[Dict[str, Any]] = None
        self._index_mtime: Optional[float] = None
        self._lock = threading.Lock()
        self.system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    # -- index ------------------------------------------------------------ #

    def index(self) -> Dict[str, Any]:
        """Return the index, reloading it when ingest.py has rewritten the file."""

        path = self.settings.index_path
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            raise RagError(
                "index_missing",
                "The RAG index has not been built; run: python3 ai-services/rag-server/ingest.py",
            ) from None
        with self._lock:
            if self._index is None or mtime != self._index_mtime:
                try:
                    index = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise RagError("index_invalid", "The RAG index could not be read; rebuild it with ingest.py") from error
                if index.get("embed_model") != self.settings.embed_model:
                    raise RagError(
                        "index_model_mismatch",
                        f"Index was built with '{index.get('embed_model')}' but the server uses "
                        f"'{self.settings.embed_model}'; rebuild it with ingest.py",
                    )
                self._index, self._index_mtime = index, mtime
            return self._index

    def index_status(self) -> Dict[str, Any]:
        try:
            index = self.index()
        except RagError as error:
            return {"ready": False, "code": error.code, "message": error.message}
        return {
            "ready": True,
            "chunks": len(index["chunks"]),
            "documents": len({chunk["source"] for chunk in index["chunks"]}),
            "embed_model": index["embed_model"],
            "created_at": index["created_at"],
            "stale": index.get("fingerprint") != knowledge_fingerprint(self.settings.knowledge_dir),
        }

    def sources(self, feature: Optional[str] = None) -> List[Dict[str, Any]]:
        if feature is not None and feature not in FEATURES and feature != SHARED:
            raise RagError("validation_error", f"'feature' must be one of: {', '.join((SHARED, *FEATURES))}", 400)
        documents: Dict[str, Dict[str, Any]] = {}
        for chunk in self.index()["chunks"]:
            if feature and chunk["feature"] != feature:
                continue
            entry = documents.setdefault(chunk["source"], {
                "source": chunk["source"], "feature": chunk["feature"], "title": chunk["title"],
                "sections": [], "chunks": 0,
            })
            entry["chunks"] += 1
            if chunk["section"] not in entry["sections"]:
                entry["sections"].append(chunk["section"])
        return list(documents.values())

    # -- retrieval -------------------------------------------------------- #

    def _ranked(self, question: str, feature: Optional[str], top_k: int) -> List[Dict[str, Any]]:
        index = self.index()
        vector = _normalise(self.client.embed(
            self.settings.embed_model, [_embedding_text(self.settings.embed_model, question, "query")])[0])
        scope = {feature, SHARED} if feature else None
        ranked = []
        for chunk in index["chunks"]:
            if scope and chunk["feature"] not in scope:
                continue
            score = sum(a * b for a, b in zip(vector, chunk["embedding"]))
            ranked.append({**{k: v for k, v in chunk.items() if k != "embedding"}, "score": round(score, 4)})
        ranked.sort(key=lambda item: (-item["score"], item["id"]))
        return ranked[:top_k]

    def retrieve(self, question: Any, feature: Any = None, top_k: Any = None) -> Dict[str, Any]:
        """Return ranked chunks with scores; never calls the chat model."""

        question, feature, top_k = validate_request(question, feature, top_k, self.settings.top_k)
        started = time.perf_counter()
        results = self._ranked(question, feature, top_k)
        return {
            "schema_version": SCHEMA_VERSION,
            "question": question,
            "feature": feature,
            "threshold": self.settings.min_score,
            "results": [
                {"id": r["id"], "source": r["source"], "feature": r["feature"], "title": r["title"],
                 "section": r["section"], "score": r["score"],
                 "relevant": r["score"] >= self.settings.min_score, "snippet": _snippet(r["text"])}
                for r in results
            ],
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }

    def query(self, question: Any, feature: Any = None, top_k: Any = None) -> Dict[str, Any]:
        """Answer from retrieved context only, or return insufficient_context."""

        question, feature, top_k = validate_request(question, feature, top_k, self.settings.top_k)
        started = time.perf_counter()
        ranked = self._ranked(question, feature, top_k)
        relevant = [chunk for chunk in ranked if chunk["score"] >= self.settings.min_score]
        retrieval = {
            "top_score": ranked[0]["score"] if ranked else None,
            "threshold": self.settings.min_score,
            "considered": len(ranked),
            "relevant": len(relevant),
        }

        def result(status: str, answer: str, confidence: str, citations: List[Dict[str, Any]],
                   reason: Optional[str]) -> Dict[str, Any]:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": status,
                "question": question,
                "feature": feature,
                "answer": answer,
                "confidence": confidence,
                "citations": citations,
                "reason": reason,
                "retrieval": retrieval,
                "models": {"embedding": self.settings.embed_model, "generation": self.settings.chat_model},
                "duration_ms": round((time.perf_counter() - started) * 1000),
            }

        if not relevant:
            return result("insufficient_context", INSUFFICIENT_MESSAGE, "none", [], "no_relevant_context")

        labelled = {f"S{number}": chunk for number, chunk in enumerate(relevant, start=1)}
        context = "\n\n".join(
            f"[{label}] {chunk['title']} - {chunk['section']} ({chunk['source']})\n{chunk['text']}"
            for label, chunk in labelled.items()
        )
        raw = self.client.chat(self.settings.chat_model, [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"Sources:\n\n{context}\n\nQuestion: {question}"},
        ]).strip()

        if not raw or INSUFFICIENT_MARKER in raw.upper().replace(" ", "_"):
            return result("insufficient_context", INSUFFICIENT_MESSAGE, "none", [], "model_declined")

        cited = extract_citations(raw)
        valid = [label for label in cited if label in labelled]
        invalid = [label for label in cited if label not in labelled]
        if not valid:
            return result("insufficient_context", INSUFFICIENT_MESSAGE, "none", [], "uncited_answer")

        answer = _strip_citations(raw, invalid)
        citations = [
            {"id": label, "source": labelled[label]["source"], "title": labelled[label]["title"],
             "section": labelled[label]["section"], "score": labelled[label]["score"],
             "snippet": _snippet(labelled[label]["text"])}
            for label in valid
        ]
        confidence = confidence_category(
            [labelled[label]["score"] for label in valid], len(relevant), len(invalid), self.settings)
        return result("answered", answer, confidence, citations,
                      "invalid_citations_removed" if invalid else None)

    def ollama_status(self) -> Dict[str, Any]:
        try:
            names = self.client.models()
        except Exception:  # noqa: BLE001 - health must never raise
            return {"reachable": False}

        def installed(model: str) -> bool:
            return any(name == model or name == f"{model}:latest" for name in names)

        return {
            "reachable": True,
            "embed_model": {"name": self.settings.embed_model, "installed": installed(self.settings.embed_model)},
            "chat_model": {"name": self.settings.chat_model, "installed": installed(self.settings.chat_model)},
        }
