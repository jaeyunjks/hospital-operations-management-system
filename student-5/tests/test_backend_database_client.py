"""Security-focused tests for the backend-to-database HTTP boundary."""

from __future__ import annotations

import pytest

import database_client
from database_client import DatabaseClient
from errors import DatabaseServiceError


@pytest.mark.parametrize("base_url", [
    "file:///tmp/database.sock",
    "ftp://localhost/database",
    "data:text/plain,database",
    "javascript:alert(1)",
])
def test_database_client_rejects_non_http_urls_before_network(
        monkeypatch, base_url):
    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("urlopen must not run for an unsupported URL scheme")

    monkeypatch.setattr(database_client.urllib.request, "urlopen", forbidden_network)
    client = DatabaseClient(base_url=base_url, timeout=1)

    with pytest.raises(DatabaseServiceError, match="must use http or https"):
        client.health()
