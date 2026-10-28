"""
test_action_relay.py - behaviour tests for the /actions/ relay routes in
frontend/app.py.

The page scripts call these routes instead of the backend, because in Azure the
backend has internal ingress only. Each route forwards one fixed backend
endpoint and hands the reply back unchanged.

Same seam as test_multi_agent_frontend.py: `requests.request` is replaced with a
MagicMock, so no socket is opened and we can assert exactly what was sent.

Scenarios covered:
  1. Each of the six routes calls the right method and backend path, with the
     X-User-Role header and the shared timeout.
  2. A JSON body is forwarded; without one, no body is sent.
  3. The backend's body, status and Content-Type come back unchanged (a 403
     stays the same 403 with the same body; the body is not parsed).
  4. Backend down: 502 with a fixed message, no address or exception text.
  5. A non-number id is a 404 and never reaches the backend.
  6. An unknown /actions/ path is a 404 and never reaches the backend.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# --- Make the frontend package importable ---------------------------------
FRONTEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "frontend")
)
if FRONTEND_DIR not in sys.path:
    sys.path.insert(0, FRONTEND_DIR)

import app as frontend_app  # noqa: E402


# ==========================================================================
# Fakes
# ==========================================================================
def _reply(status_code, content=b"{}", content_type="application/json"):
    """A stand-in for a requests.Response with just what the relay reads."""
    r = MagicMock(name="response")
    r.status_code = status_code
    r.content = content
    r.headers = {"Content-Type": content_type}
    return r


@pytest.fixture
def http(monkeypatch):
    """Replace requests.request (the only backend seam) with a MagicMock."""
    mock = MagicMock(name="requests.request")
    mock.return_value = _reply(200)
    monkeypatch.setattr(frontend_app.requests, "request", mock)
    return mock


@pytest.fixture
def client():
    frontend_app.app.config.update(TESTING=True)
    return frontend_app.app.test_client()


# The six routes: (method, path on this app, path on the backend).
RELAYS = [
    ("PUT", "/actions/care-tasks/7/acknowledge", "/api/care-tasks/7/acknowledge"),
    ("PUT", "/actions/care-tasks/7/complete", "/api/care-tasks/7/complete"),
    ("DELETE", "/actions/care-tasks/7", "/api/care-tasks/7"),
    ("PUT", "/actions/consultations/12/respond", "/api/consultations/12/respond"),
    ("DELETE", "/actions/consultations/12", "/api/consultations/12"),
    ("PUT", "/actions/ai/summaries/31/review", "/api/ai/summaries/31/review"),
]
RELAY_IDS = [f"{method}-{path}" for method, path, _ in RELAYS]


def _relay_cases():
    return pytest.mark.parametrize("method, path, backend_path", RELAYS, ids=RELAY_IDS)


# ==========================================================================
# 1. Each route calls its own backend endpoint.
# ==========================================================================
@_relay_cases()
@pytest.mark.parametrize("role", ["doctor", "nurse", "specialist"])
def test_each_route_calls_its_backend_endpoint(client, http, method, path, backend_path, role):
    resp = client.open(path, method=method, query_string={"role": role})

    assert resp.status_code == 200
    assert http.call_count == 1
    args, kwargs = http.call_args
    assert args[0] == method
    assert args[1] == frontend_app.BACKEND_API_URL + backend_path
    assert kwargs["headers"] == {"X-User-Role": role}
    assert kwargs["timeout"] == frontend_app.BACKEND_TIMEOUT


@_relay_cases()
def test_the_role_falls_back_to_the_default_like_every_other_route(
    client, http, method, path, backend_path
):
    client.open(path, method=method, query_string={"role": "admin"})

    assert http.call_args.kwargs["headers"] == {"X-User-Role": frontend_app.DEFAULT_ROLE}


# ==========================================================================
# 2. A JSON body is forwarded - and only when there is one.
# ==========================================================================
@_relay_cases()
def test_a_json_body_is_forwarded(client, http, method, path, backend_path):
    body = {"status": "accepted", "response_notes": "Seen on ward 4"}

    client.open(path, method=method, json=body)

    assert http.call_args.kwargs["json"] == body


@_relay_cases()
def test_no_body_means_no_json_is_sent(client, http, method, path, backend_path):
    client.open(path, method=method)

    assert "json" not in http.call_args.kwargs


# ==========================================================================
# 3. The backend's reply comes back unchanged.
# ==========================================================================
@_relay_cases()
@pytest.mark.parametrize(
    "status, content, content_type",
    [
        (200, b'{"care_task": {"task_id": 7, "status": "completed"}}', "application/json"),
        (403, b'{"error": "You are not the assigned nurse for this task."}', "application/json"),
        (409, b'{"error": "Discharge readiness decisions are final."}', "application/json"),
        (500, b"<html>Bad gateway</html>", "text/html; charset=utf-8"),
        (204, b"", "application/json"),
    ],
    ids=["ok", "forbidden", "conflict", "html-500", "empty-204"],
)
def test_the_backend_reply_comes_back_unchanged(
    client, http, method, path, backend_path, status, content, content_type
):
    reply = _reply(status, content, content_type)
    http.return_value = reply

    resp = client.open(path, method=method)

    assert resp.status_code == status
    assert resp.get_data() == content
    assert resp.headers["Content-Type"] == content_type
    reply.json.assert_not_called()  # passed through as bytes, never parsed


# ==========================================================================
# 4. Backend down: a fixed 502.
# ==========================================================================
@_relay_cases()
@pytest.mark.parametrize(
    "error_name", ["ConnectionError", "Timeout"], ids=["refused", "timeout"]
)
def test_backend_down_is_a_fixed_502(client, http, method, path, backend_path, error_name):
    error = getattr(frontend_app.requests, error_name)
    http.side_effect = error("connection refused by 10.0.0.9:5200 (localhost)")

    resp = client.open(path, method=method)

    assert resp.status_code == 502
    assert resp.get_json() == {"error": "Could not reach the backend."}
    text = resp.get_data(as_text=True)
    assert "10.0.0.9" not in text
    assert "5200" not in text
    assert "localhost" not in text


# ==========================================================================
# 5. Only a whole-number id is accepted.
# ==========================================================================
_BAD_IDS = ["abc", "1.5", "-1", "7abc", "%20", "..", "7%2F..%2Fadmin"]


@pytest.mark.parametrize("bad_id", _BAD_IDS)
@pytest.mark.parametrize("method, path, backend_path", RELAYS, ids=RELAY_IDS)
def test_a_non_number_id_is_a_404_and_never_reaches_the_backend(
    client, http, method, path, backend_path, bad_id
):
    # Swap the id segment (7, 12 or 31) in the route for the bad one.
    prefix, _, suffix = path.partition(path.split("/")[3])
    resp = client.open(prefix + bad_id + suffix, method=method)

    assert resp.status_code == 404
    http.assert_not_called()


def test_full_width_digits_are_read_as_a_number_and_sent_as_plain_digits(client, http):
    # Werkzeug's int converter accepts any Unicode digit. The backend path is
    # built from the parsed integer, so the text the user typed never reaches it.
    client.delete("/actions/consultations/１２")

    assert http.call_args.args[1] == frontend_app.BACKEND_API_URL + "/api/consultations/12"


# ==========================================================================
# 6. Exactly these six routes - not a general proxy.
# ==========================================================================
@pytest.mark.parametrize(
    "method, path",
    [
        ("PUT", "/actions"),
        ("PUT", "/actions/"),
        ("PUT", "/actions/unknown"),
        ("PUT", "/actions/care-tasks/7/delete"),
        ("PUT", "/actions/care-tasks/7/acknowledge/extra"),
        ("PUT", "/actions/care-tasks"),
        ("DELETE", "/actions/ai/summaries/31"),
        ("PUT", "/actions/consultations/12/cancel"),
        ("GET", "/actions/api/care-tasks/"),
        ("PUT", "/actions/api/care-tasks/7/acknowledge"),
        ("POST", "/actions/clinical-records/1"),
        ("PUT", "/actions/../api/care-tasks/7"),
    ],
)
def test_an_unknown_actions_path_is_a_404_and_never_reaches_the_backend(
    client, http, method, path
):
    resp = client.open(path, method=method)

    assert resp.status_code == 404
    http.assert_not_called()
