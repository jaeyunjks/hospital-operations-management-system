"""
test_care_task_form_frontend.py - behaviour tests for the doctor's
"raise a care task" screen in frontend/app.py (route /care-tasks/new).

The frontend is a thin passthrough: its only outbound dependency is the
`requests` library talking to the backend API on port 5200. That single seam
(`requests.request`, called from app._backend) is replaced with a MagicMock,
so no socket is ever opened and we can assert exactly what the frontend sent.

Scenarios covered:
  1. GET renders a blank form (no backend call).
  2. POST forwards the submitted fields to POST /api/care-tasks/ as JSON,
     with the X-User-Role header, and renders the backend's created task.
  3. A blank optional due_at is dropped, not sent as an empty string.
  4. A backend rejection is shown on the page and the form entries are kept.
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


def _resp(status_code, json_body):
    """A stand-in for a requests.Response with just what app._backend reads."""
    r = MagicMock(name="response")
    r.status_code = status_code
    r.ok = 200 <= status_code < 300
    r.content = b"{}"
    r.json.return_value = json_body
    return r


@pytest.fixture
def http(monkeypatch):
    """Replace requests.request (the only backend seam) with a MagicMock."""
    mock = MagicMock(name="requests.request")
    monkeypatch.setattr(frontend_app.requests, "request", mock)
    return mock


@pytest.fixture
def client():
    frontend_app.app.config.update(TESTING=True)
    return frontend_app.app.test_client()


# ==========================================================================
# 1. GET - blank form, no backend traffic.
# ==========================================================================
def test_get_renders_blank_form(client, http):
    resp = client.get("/care-tasks/new?role=doctor")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'name="clinical_record_id"' in body
    assert 'name="assigned_nurse_id"' in body
    assert 'name="task_description"' in body
    assert 'hx-post="/care-tasks/new"' in body
    http.assert_not_called()


# ==========================================================================
# 2. POST - fields forwarded to the backend, created task rendered.
# ==========================================================================
def test_post_forwards_to_backend_and_shows_result(client, http):
    http.return_value = _resp(
        201,
        {
            "care_task": {
                "task_id": 42,
                "assigned_nurse_id": 7,
                "status": "pending",
            },
            "clinical_record_id": 10,
            "patient_id": 55,
        },
    )

    resp = client.post(
        "/care-tasks/new?role=doctor",
        data={
            "clinical_record_id": "10",
            "assigned_nurse_id": "7",
            "task_description": "2-hourly obs",
            "due_at": "2026-09-10T09:00",
        },
    )

    assert resp.status_code == 200

    # The frontend called the backend's create endpoint once...
    assert http.call_count == 1
    args, kwargs = http.call_args
    assert args[0] == "POST"
    assert args[1].endswith("/api/care-tasks/")
    # ...forwarding the form as JSON, with the stand-in identity header.
    assert kwargs["json"] == {
        "clinical_record_id": "10",
        "assigned_nurse_id": "7",
        "task_description": "2-hourly obs",
        "due_at": "2026-09-10T09:00",
    }
    assert kwargs["headers"]["X-User-Role"] == "doctor"

    body = resp.get_data(as_text=True)
    assert "Task #42 created" in body
    assert "pending" in body


# ==========================================================================
# 3. A blank optional due_at must not be sent as "".
# ==========================================================================
def test_blank_due_at_is_dropped(client, http):
    http.return_value = _resp(
        201,
        {
            "care_task": {"task_id": 43, "assigned_nurse_id": 7, "status": "pending"},
            "clinical_record_id": 10,
            "patient_id": 55,
        },
    )

    client.post(
        "/care-tasks/new?role=doctor",
        data={
            "clinical_record_id": "10",
            "assigned_nurse_id": "7",
            "task_description": "Encourage fluids",
            "due_at": "",
        },
    )

    sent = http.call_args.kwargs["json"]
    assert "due_at" not in sent
    assert sent == {
        "clinical_record_id": "10",
        "assigned_nurse_id": "7",
        "task_description": "Encourage fluids",
    }


# ==========================================================================
# 4. Backend rejection is surfaced and the form entries are kept.
# ==========================================================================
def test_backend_error_is_shown_and_form_kept(client, http):
    http.return_value = _resp(404, {"error": "clinical record 999 does not exist"})

    resp = client.post(
        "/care-tasks/new?role=doctor",
        data={
            "clinical_record_id": "999",
            "assigned_nurse_id": "7",
            "task_description": "2-hourly obs",
        },
    )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "clinical record 999 does not exist" in body
    assert "Task not created" in body
    # form re-rendered with the rejected values still in place
    assert 'value="999"' in body
    assert "2-hourly obs" in body
