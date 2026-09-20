from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp.server.transport_security import TransportSecurityMiddleware

MCP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCP_ROOT))

import server as server_module


def load_clean_config(monkeypatch, **environment):
    for name in ("HOMS_MCP_HOST", "HOMS_MCP_PORT",
                 "HOMS_MCP_ALLOW_DOCKER_HOST"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return server_module.load_config()


def middleware_for(config):
    settings = server_module.build_transport_security(config)
    return settings, TransportSecurityMiddleware(settings)


def test_default_local_mode_is_loopback_only(monkeypatch):
    config = load_clean_config(monkeypatch)
    settings, middleware = middleware_for(config)

    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert config.allow_docker_host is False
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == [
        "127.0.0.1:8000", "localhost:8000", "[::1]:8000"
    ]
    assert middleware._validate_host("127.0.0.1:8000") is True
    assert middleware._validate_host("localhost:8000") is True
    assert middleware._validate_host("host.docker.internal:8000") is False
    assert middleware._validate_host("attacker.invalid:8000") is False


def test_default_origins_are_exact_loopback_values(monkeypatch):
    config = load_clean_config(monkeypatch)
    settings, middleware = middleware_for(config)

    assert settings.allowed_origins == [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://[::1]:8000",
    ]
    assert middleware._validate_origin(None) is True
    assert middleware._validate_origin("http://localhost:8000") is True
    assert middleware._validate_origin("https://attacker.invalid") is False
    assert middleware._validate_origin(
        "http://host.docker.internal:8000"
    ) is False


@pytest.mark.parametrize("value", ["true", "TRUE", " True "])
def test_docker_flag_parses_true_strictly(value, monkeypatch):
    config = load_clean_config(
        monkeypatch,
        HOMS_MCP_HOST="0.0.0.0",
        HOMS_MCP_ALLOW_DOCKER_HOST=value,
    )
    assert config.allow_docker_host is True


@pytest.mark.parametrize("value", ["", "1", "yes", "on", "disabled"])
def test_invalid_docker_flag_is_rejected(value, monkeypatch):
    with pytest.raises(ValueError, match="HOMS_MCP_ALLOW_DOCKER_HOST"):
        load_clean_config(
            monkeypatch, HOMS_MCP_ALLOW_DOCKER_HOST=value
        )


def test_docker_mode_adds_only_the_backend_host(monkeypatch):
    config = load_clean_config(
        monkeypatch,
        HOMS_MCP_HOST="0.0.0.0",
        HOMS_MCP_ALLOW_DOCKER_HOST="true",
    )
    settings, middleware = middleware_for(config)

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == [
        "127.0.0.1:8000",
        "localhost:8000",
        "[::1]:8000",
        "host.docker.internal:8000",
    ]
    assert middleware._validate_host("host.docker.internal:8000") is True
    assert middleware._validate_host("host.docker.internal:8001") is False
    assert middleware._validate_host("attacker.invalid:8000") is False
    assert "http://host.docker.internal:8000" not in settings.allowed_origins
    assert middleware._validate_origin("https://attacker.invalid") is False


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", "*"])
def test_wildcard_bind_requires_explicit_docker_access(host, monkeypatch):
    with pytest.raises(ValueError, match="wildcard bind"):
        load_clean_config(monkeypatch, HOMS_MCP_HOST=host)


def test_wildcard_bind_is_accepted_with_docker_access(monkeypatch):
    config = load_clean_config(
        monkeypatch,
        HOMS_MCP_HOST="0.0.0.0",
        HOMS_MCP_ALLOW_DOCKER_HOST="true",
    )
    assert config.host == "0.0.0.0"
    assert config.allow_docker_host is True


def test_non_default_port_is_used_for_every_allowed_value(monkeypatch):
    config = load_clean_config(
        monkeypatch,
        HOMS_MCP_HOST="0.0.0.0",
        HOMS_MCP_PORT="8765",
        HOMS_MCP_ALLOW_DOCKER_HOST="true",
    )
    settings, middleware = middleware_for(config)

    assert settings.allowed_hosts == [
        "127.0.0.1:8765",
        "localhost:8765",
        "[::1]:8765",
        "host.docker.internal:8765",
    ]
    assert settings.allowed_origins == [
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "http://[::1]:8765",
    ]
    assert middleware._validate_host("host.docker.internal:8000") is False
    assert middleware._validate_origin("http://localhost:8000") is False


def test_imported_app_uses_explicit_enabled_security():
    assert server_module.transport_security.enable_dns_rebinding_protection is True
    assert server_module.transport_security.allowed_hosts == [
        "127.0.0.1:8000", "localhost:8000", "[::1]:8000"
    ]
