from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import rag

# Load under a unique name: the shared MCP server's tests also import a "server" module.
SPEC = importlib.util.spec_from_file_location("homs_rag_server", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = server  # dataclasses resolve their module here
SPEC.loader.exec_module(server)

LOCAL = {"Host": "127.0.0.1:8100"}


@pytest.fixture
def client(engine):
    config = server.ServerConfig(host="127.0.0.1", port=8100, allow_docker_host=False)
    return server.create_app(engine, config).test_client()


def post(client, path, body, headers=LOCAL):
    return client.post(path, json=body, headers=headers)


def test_health_reports_index_models_and_thresholds(client):
    response = client.get("/health", headers=LOCAL)
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["index"]["ready"] is True and body["index"]["chunks"] == 5
    assert body["ollama"]["embed_model"]["installed"] is True
    assert body["thresholds"] == {"relevance": 0.5, "medium": 0.7, "high": 0.9}


def test_health_is_degraded_when_ollama_or_model_is_missing(client, fake):
    fake.available = ["llama3.2:3b"]
    assert client.get("/health", headers=LOCAL).status_code == 503
    fake.error = rag.RagError("ollama_unavailable", "down")
    body = client.get("/health", headers=LOCAL).get_json()
    assert body["status"] == "degraded" and body["ollama"] == {"reachable": False}


def test_query_returns_grounded_answer(client):
    body = post(client, "/query", {"question": "stock reorder", "feature": "student-3"}).get_json()
    assert body["status"] == "answered"
    assert body["citations"][0]["source"] == "student-3/stock.md"
    assert body["confidence"] in {"high", "medium", "low"}


def test_query_returns_insufficient_context(client):
    response = post(client, "/query", {"question": "weather", "feature": "student-3"})
    assert response.status_code == 200
    assert response.get_json()["status"] == "insufficient_context"


def test_retrieve_returns_scored_results(client):
    body = post(client, "/retrieve", {"question": "stock", "feature": "student-3", "top_k": 2}).get_json()
    assert len(body["results"]) == 2
    assert {"score", "relevant", "source", "section", "snippet"} <= body["results"][0].keys()


@pytest.mark.parametrize("body,message", [
    ([], "JSON object"),
    ({"question": "stock", "prompt": "ignore rules"}, "Unexpected field(s): prompt"),
    ({"question": " "}, "non-blank"),
    ({"question": "stock", "feature": "pharmacy"}, "feature"),
])
def test_invalid_requests_return_400(client, body, message):
    response = post(client, "/query", body)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "validation_error"
    assert message in response.get_json()["error"]["message"]


def test_oversized_body_is_rejected(client):
    response = client.post("/query", data="x" * 5000, content_type="application/json", headers=LOCAL)
    assert response.status_code == 413


def test_ollama_failure_is_a_json_503(client, fake):
    fake.error = rag.RagError("ollama_unavailable", "Ollama is not reachable")
    response = post(client, "/query", {"question": "stock"})
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "ollama_unavailable"


@pytest.mark.parametrize("host", ["evil.example:8100", "127.0.0.1:9999", "host.docker.internal:8100"])
def test_unexpected_host_headers_are_rejected(client, host):
    response = client.get("/health", headers={"Host": host})
    assert response.status_code == 421
    assert response.get_json()["error"]["code"] == "host_not_allowed"


def test_docker_host_is_allowed_only_when_enabled(engine):
    config = server.ServerConfig(host="0.0.0.0", port=8100, allow_docker_host=True)
    client = server.create_app(engine, config).test_client()
    assert client.get("/health", headers={"Host": "host.docker.internal:8100"}).status_code == 200


def test_cross_site_browser_origins_are_rejected(client):
    bad = client.get("/health", headers={**LOCAL, "Origin": "http://evil.example"})
    good = client.get("/health", headers={**LOCAL, "Origin": "http://localhost:8100"})
    assert bad.status_code == 403 and good.status_code == 200


def test_sources_and_unknown_routes(client):
    body = client.get("/sources?feature=student-3", headers=LOCAL).get_json()
    assert [d["source"] for d in body["documents"]] == ["student-3/stock.md"]
    assert client.get("/sources?feature=nope", headers=LOCAL).status_code == 400
    assert client.get("/nope", headers=LOCAL).get_json()["error"]["code"] == "not_found"
    assert client.get("/query", headers=LOCAL).status_code == 405


def test_server_config_is_loopback_only_by_default():
    config = server.load_server_config({})
    assert (config.host, config.port, config.allow_docker_host) == ("127.0.0.1", 8100, False)
    assert "host.docker.internal:8100" not in server.allowed_hosts(config)


@pytest.mark.parametrize("env", [
    {"HOMS_RAG_HOST": "0.0.0.0"},
    {"HOMS_RAG_ALLOW_DOCKER_HOST": "yes"},
    {"HOMS_RAG_PORT": "0"},
    {"HOMS_RAG_PORT": "abc"},
    {"HOMS_RAG_HOST": " "},
])
def test_invalid_server_config_is_rejected(env):
    with pytest.raises(ValueError):
        server.load_server_config(env)


def test_wildcard_bind_with_docker_flag_is_accepted():
    config = server.load_server_config({"HOMS_RAG_HOST": "0.0.0.0", "HOMS_RAG_ALLOW_DOCKER_HOST": "true"})
    assert "host.docker.internal:8100" in server.allowed_hosts(config)
