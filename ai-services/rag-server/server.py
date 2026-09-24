#!/usr/bin/env python3
"""Shared, non-containerised HOMS RAG server.

Run from the repository root with::

    python3 ai-services/rag-server/server.py
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request

from rag import FEATURES, RagEngine, RagError, SCHEMA_VERSION, load_settings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8100
WILDCARD_HOSTS = {"0.0.0.0", "::", "[::]", "*"}
REQUEST_FIELDS = {"question", "feature", "top_k"}
MAX_BODY_BYTES = 4096

logger = logging.getLogger("homs.rag")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int
    allow_docker_host: bool


def load_server_config(env=None) -> ServerConfig:
    """Loopback-only by default; Docker access must be explicitly enabled."""

    env = os.environ if env is None else env
    host = env.get("HOMS_RAG_HOST", DEFAULT_HOST).strip()
    if not host:
        raise ValueError("HOMS_RAG_HOST must not be blank")
    try:
        port = int(env.get("HOMS_RAG_PORT", str(DEFAULT_PORT)))
    except ValueError as error:
        raise ValueError("HOMS_RAG_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("HOMS_RAG_PORT must be between 1 and 65535")
    raw_allow = env.get("HOMS_RAG_ALLOW_DOCKER_HOST", "false").strip().lower()
    if raw_allow not in {"true", "false"}:
        raise ValueError("HOMS_RAG_ALLOW_DOCKER_HOST must be 'true' or 'false'")
    allow_docker_host = raw_allow == "true"
    if host in WILDCARD_HOSTS and not allow_docker_host:
        raise ValueError("HOMS_RAG_ALLOW_DOCKER_HOST must be true when HOMS_RAG_HOST uses a wildcard bind")
    return ServerConfig(host=host, port=port, allow_docker_host=allow_docker_host)


def allowed_hosts(config: ServerConfig) -> set[str]:
    hosts = {f"127.0.0.1:{config.port}", f"localhost:{config.port}", f"[::1]:{config.port}"}
    if config.allow_docker_host:
        hosts.add(f"host.docker.internal:{config.port}")
    return hosts


def _error(code: str, message: str, status: int, details: Optional[Dict[str, Any]] = None):
    body = {"schema_version": SCHEMA_VERSION, "error": {"code": code, "message": message, "details": details or {}}}
    return jsonify(body), status


def _payload() -> Dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise RagError("validation_error", "Request body must be a JSON object", 400)
    unexpected = sorted(set(payload) - REQUEST_FIELDS)
    if unexpected:
        raise RagError("validation_error", f"Unexpected field(s): {', '.join(unexpected)}", 400)
    return payload


def create_app(engine: RagEngine, config: ServerConfig) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    hosts = allowed_hosts(config)
    origins = {f"http://{host}" for host in hosts if not host.startswith("host.docker.internal")}

    @app.before_request
    def enforce_local_access():
        # DNS-rebinding and browser cross-site protection, matching the MCP server.
        if request.host not in hosts:
            return _error("host_not_allowed", "Host header is not allowed", 421)
        origin = request.headers.get("Origin")
        if origin and origin not in origins:
            return _error("origin_not_allowed", "Browser origin is not allowed", 403)
        return None

    @app.errorhandler(RagError)
    def rag_error(error: RagError):
        return _error(error.code, error.message, error.status)

    @app.errorhandler(413)
    def too_large(_exc):
        return _error("validation_error", f"Request body must be at most {MAX_BODY_BYTES} bytes", 413)

    @app.errorhandler(404)
    def not_found(_exc):
        return _error("not_found", "Unknown endpoint", 404)

    @app.errorhandler(405)
    def wrong_method(_exc):
        return _error("method_not_allowed", "Method not allowed for this endpoint", 405)

    @app.get("/health")
    def health():
        index = engine.index_status()
        ollama = engine.ollama_status()
        ready = index["ready"] and ollama.get("reachable") and all(
            ollama[key]["installed"] for key in ("embed_model", "chat_model"))
        return jsonify({
            "schema_version": SCHEMA_VERSION,
            "status": "ok" if ready else "degraded",
            "service": "homs-rag-server",
            "features": list(FEATURES),
            "thresholds": {"relevance": engine.settings.min_score, "medium": engine.settings.medium_score,
                           "high": engine.settings.high_score},
            "index": index,
            "ollama": ollama,
        }), 200 if ready else 503

    @app.get("/sources")
    def sources():
        feature = request.args.get("feature") or None
        documents = engine.sources(feature)
        return jsonify({"schema_version": SCHEMA_VERSION, "feature": feature, "documents": documents})

    @app.post("/retrieve")
    def retrieve():
        payload = _payload()
        result = engine.retrieve(payload.get("question"), payload.get("feature"), payload.get("top_k"))
        top = result["results"][0]["score"] if result["results"] else None
        logger.info("[RAG] retrieve feature=%s top_score=%s results=%s duration_ms=%s",
                    result["feature"] or "all", top, len(result["results"]), result["duration_ms"])
        return jsonify(result)

    @app.post("/query")
    def query():
        payload = _payload()
        result = engine.query(payload.get("question"), payload.get("feature"), payload.get("top_k"))
        logger.info("[RAG] query feature=%s status=%s confidence=%s top_score=%s cited=%s reason=%s duration_ms=%s",
                    result["feature"] or "all", result["status"], result["confidence"],
                    result["retrieval"]["top_score"], len(result["citations"]), result["reason"],
                    result["duration_ms"])
        return jsonify(result)

    return app


def main() -> None:
    try:
        config = load_server_config()
        engine = RagEngine(load_settings())
    except ValueError as error:
        raise SystemExit(f"Configuration error: {error}") from None
    status = engine.index_status()
    if status["ready"]:
        stale = " (STALE: knowledge changed, run ingest.py)" if status["stale"] else ""
        logger.info("[RAG] index: %s chunks from %s documents%s", status["chunks"], status["documents"], stale)
    else:
        logger.info("[RAG] index not ready: %s", status["message"])
    logger.info("[RAG] listening on http://%s:%s (docker access %s)", config.host, config.port,
                "enabled" if config.allow_docker_host else "disabled")
    create_app(engine, config).run(host=config.host, port=config.port, threaded=True)


if __name__ == "__main__":
    main()
