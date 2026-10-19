"""Service addresses come from configuration and must be plain http(s) URLs.

Every outbound call in this backend uses ``urllib``, which would also open
``file:`` or custom-scheme URLs. Checking the configured base URL once at
start-up keeps those calls limited to HTTP services.
"""
from urllib.parse import urlparse


def http_url(value: str, setting: str) -> str:
    """Return ``value`` without a trailing slash, or fail if it is not http(s)."""
    candidate = (value or "").strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{setting} must be an http:// or https:// URL")
    return candidate
