"""
test_multi_agent_frontend.py - behaviour tests for the HTMX fragments in
frontend/app.py: the four discharge-review routes (/ai/discharge-review...),
plus /ai/care-tasks and /ai/policy-question.

The frontend's only outbound dependency is `requests.request`, called from
app._backend. That one seam is replaced with a MagicMock, so no socket is ever
opened and we can assert exactly what the frontend sent (or that it sent
nothing at all). Every fragment is checked as rendered HTML from the Flask
test client.

Discharge review, four routes:
  latest    GET  /ai/discharge-review?admission_id=N
  start     POST /ai/discharge-review/start
  progress  GET  /ai/discharge-review/<workflow_id>
  decision  POST /ai/discharge-review/<workflow_id>/decision

Scenarios covered:
  1. Each route calls its own backend endpoint with the right shape.
  2. Each state - progress, review, finished, failed, disabled, unavailable,
     forbidden, error - comes back from every route that can return it;
     start comes back from latest only.
  3. A bad workflow id is answered by the frontend and never reaches the backend.
  4. The progress piece refreshes itself only while the workflow is running.
  5. Approve and Reject are on the review piece and not on a finished one.
  6. Text that came from the backend (or the query string) is escaped.
  7. /ai/care-tasks and /ai/policy-question: two tests each.
"""

import os
import re
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
def _resp(status_code, json_body, content=b"{}"):
    """A stand-in for a requests.Response with just what app._backend reads."""
    r = MagicMock(name="response")
    r.status_code = status_code
    r.ok = 200 <= status_code < 300
    r.content = content
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


# --- Backend replies, in the shapes routes/multi_agent_routes.py sends ------
WF_ID = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"
ADMISSION_ID = 12

_CHECKS = [
    {"id": "no_open_care_tasks", "label": "No open care tasks", "passed": True},
    {"id": "no_pending_consultations", "label": "No pending consultations", "passed": True},
    {"id": "no_scheduled_surgery", "label": "No scheduled surgery", "passed": False},
]


def _stages(planner=True, worker=True, reviewer=True):
    return [
        {"name": "planner", "done": planner, "source": "rule-based" if planner else None},
        {"name": "worker", "done": worker, "source": "llama3" if worker else None},
        {"name": "reviewer", "done": reviewer, "source": "llama3" if reviewer else None},
    ]


def _workflow(status="awaiting_human_review", **fields):
    """A finished-agents workflow: checks, gap and recommendation all present."""
    wf = {
        "workflow_id": WF_ID,
        "status": status,
        "admission_id": ADMISSION_ID,
        "stages": _stages(),
        "outcome": "not_ready",
        "checks": [dict(c) for c in _CHECKS],
        "gap": {"current": 2, "target": 3},
        "recommendation": {
            "text": "Hold discharge until the surgery is cleared.",
            "risks": ["Surgery still scheduled"],
        },
        "decision": None,
    }
    wf.update(fields)
    return wf


def _running_workflow(**fields):
    wf = _workflow(
        "in_progress",
        stages=_stages(planner=True, worker=False, reviewer=False),
        outcome=None,
        checks=[],
        gap=None,
        recommendation=None,
    )
    wf.update(fields)
    return wf


_DECISION_FOR_STATUS = {
    "approved": "approve",
    "corrected": "correct",
    "rejected": "reject",
    "partially_accepted": "partially_accept",
}


def _finished_workflow(status, comment="Agreed with the Reviewer.", **fields):
    return _workflow(
        status,
        decision={"decision": _DECISION_FOR_STATUS[status], "comment": comment},
        **fields,
    )


def _ok(workflow, **extra):
    return _resp(200, {"status": "ok", "workflow": workflow, **extra})


def _message(http_status, status, code, message):
    return _resp(http_status, {"status": status, "error": {"code": code, "message": message}})


# --- The four routes --------------------------------------------------------
ROUTES = ("latest", "start", "progress", "decision")


def _call(client, route, workflow_id=WF_ID):
    """Call one of the four discharge-review routes as a doctor."""
    if route == "latest":
        return client.get(f"/ai/discharge-review?admission_id={ADMISSION_ID}&role=doctor")
    if route == "start":
        return client.post(
            "/ai/discharge-review/start?role=doctor",
            data={"admission_id": str(ADMISSION_ID)},
        )
    if route == "progress":
        return client.get(f"/ai/discharge-review/{workflow_id}?role=doctor")
    return client.post(
        f"/ai/discharge-review/{workflow_id}/decision?role=doctor",
        data={"decision": "approve", "comment": ""},
    )


def _get(client, route, http, reply, **kwargs):
    """Set the backend's reply, call the route, return (response, body)."""
    http.return_value = reply
    resp = _call(client, route, **kwargs)
    assert resp.status_code == 200  # HTMX does not swap 4xx/5xx
    return resp, resp.get_data(as_text=True)


def _decision_values(body):
    """The `value` of every decision button on the fragment."""
    return re.findall(r'<button[^>]*name="decision"[^>]*value="([^"]*)"', body)


def _has_self_refresh(body):
    return any(marker in body for marker in ("hx-get", "hx-trigger", "delay:"))


def _has_controls(body):
    return any(marker in body for marker in ("<form", "<button", "<textarea", "hx-post"))


NEW_CHECK_BUTTON = "Run a new check"
NEW_CHECK_HINT = "Starts a fresh check with the admission's current data"


def _assert_only_new_check_form(body, admission_id=ADMISSION_ID):
    """The one control on the piece is the "Run a new check" form for this admission."""
    assert body.count("<form") == 1
    assert body.count("<button") == 1 and "<textarea" not in body
    assert body.count("hx-post") == 1
    assert 'hx-post="/ai/discharge-review/start"' in body
    assert 'hx-target="closest .discharge-review"' in body
    assert 'hx-swap="outerHTML"' in body
    assert 'hx-disabled-elt="find button"' in body
    assert f'<input type="hidden" name="admission_id" value="{admission_id}">' in body
    assert f'class="btn-secondary" type="submit">{NEW_CHECK_BUTTON}</button>' in body
    assert NEW_CHECK_HINT in body
    assert _decision_values(body) == []


ATTACK = '"><img src=x onerror=alert(1)><script>alert(1)</script>'


def _assert_escaped(body):
    """No raw markup from ATTACK survived, and the text is shown escaped."""
    assert "<img" not in body
    assert "<script" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


# ==========================================================================
# 1. Each route calls its own backend endpoint.
# ==========================================================================
@pytest.mark.parametrize(
    "route, method, path, extra",
    [
        ("latest", "GET", "/api/multi-agent/latest", {"params": {"admission_id": ADMISSION_ID}}),
        ("start", "POST", "/api/multi-agent/start", {"json": {"admission_id": ADMISSION_ID}}),
        ("progress", "GET", f"/api/multi-agent/workflows/{WF_ID}", {}),
        (
            "decision",
            "POST",
            f"/api/multi-agent/workflows/{WF_ID}/review",
            {"json": {"decision": "approve", "comment": ""}},
        ),
    ],
)
def test_each_route_calls_its_backend_endpoint(client, http, route, method, path, extra):
    _get(client, route, http, _ok(_running_workflow()))

    assert http.call_count == 1
    args, kwargs = http.call_args
    assert args[0] == method
    assert args[1].endswith(path)
    assert kwargs["headers"]["X-User-Role"] == "doctor"
    assert kwargs["timeout"] == frontend_app.DISCHARGE_REVIEW_TIMEOUT
    for key, value in extra.items():
        assert kwargs[key] == value  # whole numbers, not strings


def test_decision_forwards_the_form_and_defaults_missing_fields_to_empty(client, http):
    http.return_value = _ok(_finished_workflow("corrected", "Fix the bed number."), logged=True)

    client.post(
        f"/ai/discharge-review/{WF_ID}/decision?role=doctor",
        data={"decision": "correct", "comment": "Fix the bed number."},
    )
    assert http.call_args.kwargs["json"] == {"decision": "correct", "comment": "Fix the bed number."}

    client.post(f"/ai/discharge-review/{WF_ID}/decision?role=doctor", data={})
    # The backend owns the rules, so an empty decision goes to it to be rejected.
    assert http.call_args.kwargs["json"] == {"decision": "", "comment": ""}


# ==========================================================================
# 2. States
# ==========================================================================
def test_latest_with_no_review_offers_to_start_one(client, http):
    _, body = _get(client, "latest", http, _resp(200, {"status": "none"}))

    assert "discharge-review--start" in body
    assert f", admission {ADMISSION_ID}" in body
    assert 'hx-post="/ai/discharge-review/start"' in body
    assert 'name="admission_id"' in body and f'value="{ADMISSION_ID}"' in body
    assert "Check discharge readiness" in body
    assert "Advisory only. The assigned doctor decides." in body
    assert "AI review" in body  # nothing generated yet, so not "AI-generated"
    assert "AI-generated" not in body
    assert not _has_self_refresh(body)


def test_start_state_without_an_admission_id_has_no_admission_in_the_title(client, http):
    http.return_value = _resp(200, {"status": "none"})

    resp = client.get("/ai/discharge-review?role=doctor")

    body = resp.get_data(as_text=True)
    assert "discharge-review--start" in body
    assert ", admission" not in body
    assert 'value=""' in body


@pytest.mark.parametrize("route", ROUTES)
def test_progress_state(client, http, route):
    _, body = _get(client, route, http, _ok(_running_workflow()))

    assert "discharge-review--progress" in body
    assert "Planner, Worker and Reviewer are working" in body
    assert f", admission {ADMISSION_ID}" in body
    assert "AI review" in body
    # one line per stage, each with its word
    assert body.count("stage-list__item") == 3
    assert body.count("status--success") == 1 and body.count("status--pending") == 2
    assert body.count(">done<") == 1 and body.count(">working<") == 2
    for name in ("Planner", "Worker", "Reviewer"):
        assert f"<span>{name}</span>" in body
    assert not _has_controls(body)  # the only hx- attribute is the refresh


@pytest.mark.parametrize("route", ROUTES)
def test_review_state(client, http, route):
    _, body = _get(client, route, http, _ok(_workflow()))

    assert "discharge-review--review" in body
    assert "AI-generated" in body
    assert f", admission {ADMISSION_ID}" in body
    assert "Advisory only. The assigned doctor decides." in body
    # result line counts the blocked checks
    assert 'Not ready, <span class="u-mono">1</span> item to review' in body
    assert "No open care tasks" in body and "Verified" in body and "Blocked" in body
    assert "Hold discharge until the surgery is cleared." in body
    assert "Surgery still scheduled" in body
    assert "2</span> of <span" in body and "checks passed" in body
    assert "rule-based" in body and "llama3" in body
    # the decision form posts to this workflow's decision route
    assert f'hx-post="/ai/discharge-review/{WF_ID}/decision"' in body
    assert "Decision recorded" not in body  # no decision has been made yet


def test_review_state_ready_outcome_and_missing_pieces(client, http):
    wf = _workflow(outcome="ready", checks=[], gap=None, recommendation=None)

    _, body = _get(client, "progress", http, _ok(wf))

    assert "Ready for review" in body
    assert "No recommendation" in body
    assert "No risks listed" in body
    assert "no check results" in body


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("status", list(_DECISION_FOR_STATUS))
def test_finished_state(client, http, route, status):
    wf = _finished_workflow(status, "Agreed with the Reviewer.")

    _, body = _get(client, route, http, _ok(wf))

    assert "discharge-review--review" in body
    assert "Doctor's decision" in body
    assert _DECISION_FOR_STATUS[status].replace("_", " ").capitalize() in body
    assert "Agreed with the Reviewer." in body
    assert "Hold discharge until the surgery is cleared." in body  # result still shown
    # a new run can be started, after the doctor's decision
    _assert_only_new_check_form(body)
    assert body.index("Doctor's decision") < body.index("<form")


def test_finished_state_without_a_comment_or_decision(client, http):
    _, body = _get(
        client, "progress", http,
        _ok(_workflow("approved", decision={"decision": "approve", "comment": "  "})),
    )
    assert "No comment" in body

    _, body = _get(client, "progress", http, _ok(_workflow("approved", decision=None)))
    assert "No decision shown" in body


@pytest.mark.parametrize("route", ROUTES)
def test_failed_state(client, http, route):
    wf = _workflow("failed", outcome=None, checks=[], gap=None, recommendation=None)

    _, body = _get(client, route, http, _ok(wf))

    assert "discharge-review--message" in body
    assert f"Discharge readiness check failed, admission {ADMISSION_ID}" in body
    assert "alert--danger" in body and "status--danger" in body
    assert "ai-panel" not in body
    # The one control is the form that starts a new check for this admission.
    _assert_only_new_check_form(body)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("admission_id", [None, ""], ids=["null", "empty"])
def test_failed_state_without_an_admission_id_has_no_form(client, http, route, admission_id):
    wf = _workflow("failed", admission_id=admission_id, outcome=None, checks=[], gap=None,
                   recommendation=None)

    _, body = _get(client, route, http, _ok(wf))

    assert "Discharge readiness check failed" in body
    assert ", admission" not in body
    assert NEW_CHECK_BUTTON not in body and NEW_CHECK_HINT not in body
    assert not _has_controls(body)


def test_failed_state_with_no_admission_id_key_has_no_form(client, http):
    wf = _workflow("failed", outcome=None, checks=[], gap=None, recommendation=None)
    del wf["admission_id"]

    _, body = _get(client, "latest", http, _ok(wf))

    assert "Discharge readiness check failed" in body
    assert not _has_controls(body)


# Each message state: reply, banner title, banner colour, the backend text shown.
_MESSAGE_STATES = {
    "disabled": (
        _message(503, "disabled", "multi_agent_disabled", "Multi-Agent review is not enabled."),
        "Discharge readiness check is switched off",
        "status--pending",
        "Multi-Agent review is not enabled.",
    ),
    "unavailable": (
        _message(503, "unavailable", "multi_agent_unavailable",
                 "The Multi-Agent Server is temporarily unavailable."),
        "Discharge readiness check is unavailable",
        "alert--warning",
        "The Multi-Agent Server is temporarily unavailable.",
    ),
    "forbidden": (
        _message(403, "error", "forbidden", "You are not the assigned doctor for this admission."),
        "Discharge readiness check is limited to the assigned doctor",
        "alert--info",
        "You are not the assigned doctor for this admission.",
    ),
    "error": (
        _message(502, "error", "multi_agent_invalid_response",
                 "The Multi-Agent Server returned an invalid response."),
        "Discharge readiness check returned an error",
        "alert--danger",
        "The Multi-Agent Server returned an invalid response.",
    ),
}
_ALL_TITLES = [titles[1] for titles in _MESSAGE_STATES.values()]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("state", list(_MESSAGE_STATES))
def test_message_states(client, http, route, state):
    reply, title, tone, backend_text = _MESSAGE_STATES[state]

    _, body = _get(client, route, http, reply)

    assert "discharge-review--message" in body
    assert title in body
    assert tone in body
    assert backend_text in body  # second line, never the title
    for other in _ALL_TITLES:
        if other != title:
            assert other not in body
    assert ("multi_agent_invalid_response" in body) == (state == "error")  # code: error only
    assert "ai-panel" not in body
    assert not _has_controls(body)
    assert not _has_self_refresh(body)


@pytest.mark.parametrize("route", ROUTES)
def test_backend_unreachable_is_an_error_banner_not_a_500(client, http, route):
    http.side_effect = frontend_app.requests.ConnectionError("connection refused by 10.0.0.9")

    _, body = _get(client, route, http, http.return_value)

    assert "Discharge readiness check returned an error" in body
    assert "The assistant could not be reached. Please try again." in body
    assert "10.0.0.9" not in body  # transport details stay out of the page


@pytest.mark.parametrize("route", ROUTES)
def test_a_body_that_is_not_json_is_an_error_banner(client, http, route):
    reply = _resp(502, None, content=b"<html>Bad gateway</html>")
    reply.json.side_effect = ValueError("not json")

    _, body = _get(client, route, http, reply)

    assert "Discharge readiness check returned an error" in body
    assert "Bad gateway" not in body


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "workflow_status", ["unknown", "running", "", None, 7],
    ids=["unknown", "other-word", "empty", "none", "not-a-string"],
)
def test_a_workflow_status_we_do_not_know_is_shown_as_an_error(client, http, route, workflow_status):
    _, body = _get(client, route, http, _ok(_workflow(workflow_status)))

    assert "Discharge readiness check returned an error" in body
    assert "The assistant returned an unexpected reply." in body
    assert not _has_controls(body)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "body_json",
    [{"status": "ok"}, {"status": "ok", "workflow": "x"}, {"status": "weird"}, ["list"]],
    ids=["no-workflow", "workflow-not-a-mapping", "unknown-status", "not-a-mapping"],
)
def test_an_unusable_reply_is_an_error_banner(client, http, route, body_json):
    _, body = _get(client, route, http, _resp(200, body_json))

    assert "Discharge readiness check returned an error" in body
    assert not _has_controls(body)


@pytest.mark.parametrize("route", ["start", "progress", "decision"])
def test_a_none_reply_only_means_start_on_the_latest_route(client, http, route):
    _, body = _get(client, route, http, _resp(200, {"status": "none"}))

    assert "discharge-review--start" not in body
    assert "returned an error" in body


@pytest.mark.parametrize("route", ROUTES)
def test_every_state_is_http_200_even_when_the_backend_is_not(client, http, route):
    for reply, *_ in _MESSAGE_STATES.values():
        http.return_value = reply
        assert _call(client, route).status_code == 200


# ==========================================================================
# 2b. The decision reply: the "recorded" banner.
# ==========================================================================
def test_decision_reply_confirms_the_decision(client, http):
    wf = _finished_workflow("partially_accepted", "Mostly right.")

    _, body = _get(client, "decision", http, _ok(wf, logged=True))

    assert "Decision recorded, admission 12" in body
    assert "Decision: Partially accept" in body
    assert "alert--success" in body
    assert "The review log was not saved" not in body
    assert _decision_values(body) == []  # and the decision buttons are gone
    # The form to start a new check sits below the decision, outside the banner.
    _assert_only_new_check_form(body)
    assert body.index("Doctor's decision") < body.index("<form")
    assert body.index("Decision: Partially accept") < body.index("Doctor's decision")


def test_decision_reply_says_when_the_log_was_not_saved(client, http):
    _, body = _get(client, "decision", http, _ok(_finished_workflow("approved"), logged=False))

    assert "Decision recorded" in body
    assert "The review log was not saved" in body
    assert "alert--warning" in body and "alert--success" not in body


@pytest.mark.parametrize("logged", [None, "true", 1, "yes"], ids=["absent", "str", "int", "word"])
def test_no_recorded_banner_unless_logged_is_a_real_boolean(client, http, logged):
    reply = _ok(_finished_workflow("approved"), **({} if logged is None else {"logged": logged}))

    _, body = _get(client, "progress", http, reply)

    assert "Decision recorded" not in body
    assert "The review log was not saved" not in body


# ==========================================================================
# 3. A bad workflow id never reaches the backend.
# ==========================================================================
_BAD_IDS = {
    "word": "not-a-uuid",
    "number": "12345",
    "too-short": WF_ID[:-1],
    "too-long": WF_ID + "0",
    "trailing-text": WF_ID + "x",
    "no-dashes": WF_ID.replace("-", ""),
    "non-hex": "zzzzzzzz" + WF_ID[8:],
    "leading-space": "%20" + WF_ID,
    "trailing-newline": WF_ID + "%0A",
    "fullwidth-digit": "１" + WF_ID[1:],
}
# Ids that carry a path: the router refuses these before the view runs.
_PATH_IDS = {
    "dot-dot": "..",
    "encoded-slash": WF_ID + "%2F..%2F..%2Fadmin",
    "other-endpoint": "status%2Fx",
}


@pytest.mark.parametrize("route", ["progress", "decision"])
@pytest.mark.parametrize("bad_id", list(_BAD_IDS.values()), ids=list(_BAD_IDS))
def test_a_bad_workflow_id_is_answered_without_calling_the_backend(client, http, route, bad_id):
    resp = _call(client, route, workflow_id=bad_id)

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "That discharge review id is not valid." in body
    assert "Discharge readiness check returned an error" in body
    assert not _has_controls(body)
    assert not _has_self_refresh(body)
    http.assert_not_called()


@pytest.mark.parametrize("route", ["progress", "decision"])
@pytest.mark.parametrize("bad_id", list(_PATH_IDS.values()), ids=list(_PATH_IDS))
def test_an_id_carrying_a_path_never_reaches_the_backend(client, http, route, bad_id):
    resp = _call(client, route, workflow_id=bad_id)

    assert resp.status_code in (200, 404)
    http.assert_not_called()


def test_a_bad_decision_is_not_forwarded_either(client, http):
    resp = client.post(
        "/ai/discharge-review/not-a-uuid/decision?role=doctor",
        data={"decision": "approve", "comment": "ok"},
    )

    assert resp.status_code == 200
    http.assert_not_called()


def test_an_upper_case_uuid_is_a_valid_id(client, http):
    http.return_value = _ok(_running_workflow())

    resp = client.get(f"/ai/discharge-review/{WF_ID.upper()}?role=doctor")

    assert resp.status_code == 200
    assert http.call_args.args[1].endswith(f"/api/multi-agent/workflows/{WF_ID.upper()}")


# ==========================================================================
# 4. The progress piece refreshes itself only while the workflow is running.
# ==========================================================================
@pytest.mark.parametrize("route", ROUTES)
def test_a_running_workflow_asks_for_itself_again(client, http, route):
    _, body = _get(client, route, http, _ok(_running_workflow()))

    assert f'hx-get="/ai/discharge-review/{WF_ID}"' in body
    assert 'hx-trigger="load delay:3s"' in body
    assert 'hx-swap="outerHTML"' in body


_NOT_RUNNING = {
    "awaiting": lambda: _ok(_workflow()),
    "approved": lambda: _ok(_finished_workflow("approved"), logged=True),
    "corrected": lambda: _ok(_finished_workflow("corrected")),
    "rejected": lambda: _ok(_finished_workflow("rejected")),
    "partially_accepted": lambda: _ok(_finished_workflow("partially_accepted")),
    "failed": lambda: _ok(_workflow("failed")),
    "disabled": lambda: _MESSAGE_STATES["disabled"][0],
    "unavailable": lambda: _MESSAGE_STATES["unavailable"][0],
    "forbidden": lambda: _MESSAGE_STATES["forbidden"][0],
    "error": lambda: _MESSAGE_STATES["error"][0],
    "unknown-status": lambda: _ok(_workflow("unknown")),
    "no-workflow": lambda: _resp(200, {"status": "ok"}),
}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("make_reply", list(_NOT_RUNNING.values()), ids=list(_NOT_RUNNING))
def test_nothing_else_refreshes_itself(client, http, route, make_reply):
    _, body = _get(client, route, http, make_reply())

    assert not _has_self_refresh(body)


def test_the_start_piece_does_not_refresh_itself(client, http):
    _, body = _get(client, "latest", http, _resp(200, {"status": "none"}))

    assert not _has_self_refresh(body)


@pytest.mark.parametrize(
    "workflow_id", [None, "", 123, ["x"]], ids=["null", "empty", "number", "list"]
)
def test_a_running_workflow_without_a_usable_id_does_not_wait_forever(client, http, workflow_id):
    wf = _running_workflow(workflow_id=workflow_id)

    _, body = _get(client, "progress", http, _ok(wf))

    assert "Discharge readiness check returned an error" in body
    assert "discharge-review--progress" not in body
    assert not _has_self_refresh(body)


def test_a_running_workflow_with_no_id_key_does_not_wait_forever(client, http):
    wf = _running_workflow()
    del wf["workflow_id"]

    _, body = _get(client, "progress", http, _ok(wf))

    assert "Discharge readiness check returned an error" in body
    assert not _has_self_refresh(body)


# ==========================================================================
# 5. Approve and Reject: both on the review piece, neither on a finished one.
# ==========================================================================
@pytest.mark.parametrize("route", ROUTES)
def test_review_piece_has_approve_and_reject(client, http, route):
    _, body = _get(client, route, http, _ok(_workflow()))

    values = _decision_values(body)
    assert "approve" in values
    assert "reject" in values
    assert "Approve review" in body
    assert "Reject review" in body
    assert 'name="comment"' in body


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("status", list(_DECISION_FOR_STATUS))
def test_finished_piece_has_neither(client, http, route, status):
    _, body = _get(client, route, http, _ok(_finished_workflow(status), logged=True))

    assert _decision_values(body) == []
    assert "Approve review" not in body
    assert "Reject review" not in body
    assert "<textarea" not in body
    assert "/decision" not in body
    _assert_only_new_check_form(body)  # the only control left


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("admission_id", [None, ""], ids=["null", "empty"])
def test_finished_piece_without_an_admission_id_has_no_form(client, http, route, admission_id):
    wf = _finished_workflow("approved", admission_id=admission_id)

    _, body = _get(client, route, http, _ok(wf))

    assert "Doctor's decision" in body
    assert NEW_CHECK_BUTTON not in body
    assert not _has_controls(body)


@pytest.mark.parametrize("route", ROUTES)
def test_awaiting_review_has_only_the_four_decision_buttons(client, http, route):
    _, body = _get(client, route, http, _ok(_workflow()))

    assert _decision_values(body) == ["approve", "correct", "partially_accept", "reject"]
    assert body.count("<button") == 4
    assert body.count("<form") == 1  # the decision form, nothing for a new check
    assert NEW_CHECK_BUTTON not in body and NEW_CHECK_HINT not in body
    assert "/ai/discharge-review/start" not in body


@pytest.mark.parametrize("route", ROUTES)
def test_no_new_check_form_while_running_or_on_other_banners(client, http, route):
    _, body = _get(client, route, http, _ok(_running_workflow()))
    assert NEW_CHECK_BUTTON not in body and "/ai/discharge-review/start" not in body

    for reply, *_ in _MESSAGE_STATES.values():
        _, body = _get(client, route, http, reply)
        assert NEW_CHECK_BUTTON not in body and "/ai/discharge-review/start" not in body


def test_an_awaiting_review_without_an_id_has_no_dead_form(client, http):
    wf = _workflow()
    del wf["workflow_id"]

    _, body = _get(client, "latest", http, _ok(wf))

    assert "Discharge readiness check returned an error" in body
    assert _decision_values(body) == []
    assert not _has_controls(body)


# ==========================================================================
# 6. Text from the backend is escaped.
# ==========================================================================
@pytest.mark.parametrize("route", ROUTES)
def test_review_text_is_escaped(client, http, route):
    wf = _workflow(
        admission_id=ATTACK,
        checks=[{"id": "x", "label": ATTACK, "passed": False}],
        recommendation={"text": ATTACK, "risks": [ATTACK]},
        stages=[{"name": ATTACK, "done": True, "source": ATTACK}],
    )

    _, body = _get(client, route, http, _ok(wf))

    _assert_escaped(body)
    assert body.count("&lt;script&gt;") >= 5  # label, text, risk, name, source, id...


@pytest.mark.parametrize("route", ROUTES)
def test_finished_review_text_is_escaped(client, http, route):
    wf = _finished_workflow("corrected", comment=ATTACK)

    _, body = _get(client, route, http, _ok(wf, logged=True))

    _assert_escaped(body)


@pytest.mark.parametrize("route", ROUTES)
def test_progress_text_is_escaped(client, http, route):
    wf = _running_workflow(
        admission_id=ATTACK,
        stages=[{"name": ATTACK, "done": False, "source": None}],
    )

    _, body = _get(client, route, http, _ok(wf))

    _assert_escaped(body)


@pytest.mark.parametrize("route", ROUTES)
def test_a_hostile_workflow_id_in_a_reply_cannot_break_the_refresh_attribute(client, http, route):
    wf = _running_workflow(workflow_id=ATTACK)

    _, body = _get(client, route, http, _ok(wf))

    assert "<img" not in body
    assert "<script" not in body
    assert 'hx-get="/ai/discharge-review/%22%3E%3Cimg' in body  # percent-encoded

    # The same goes for the decision form's address.
    _, body = _get(client, route, http, _ok(_workflow(workflow_id=ATTACK)))
    assert "<img" not in body
    assert "<script" not in body
    assert 'hx-post="/ai/discharge-review/%22%3E%3Cimg' in body


@pytest.mark.parametrize("route", ROUTES)
def test_failed_text_is_escaped(client, http, route):
    wf = _workflow("failed", admission_id=ATTACK)

    _, body = _get(client, route, http, _ok(wf))

    _assert_escaped(body)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "reply_status, code",
    [("disabled", "multi_agent_disabled"), ("unavailable", "x"), ("error", "forbidden"), ("error", ATTACK)],
    ids=["disabled", "unavailable", "forbidden", "error"],
)
def test_message_text_is_escaped(client, http, route, reply_status, code):
    _, body = _get(client, route, http, _message(502, reply_status, code, ATTACK))

    _assert_escaped(body)


def test_recorded_banner_text_is_escaped(client, http):
    wf = _finished_workflow("approved", admission_id=ATTACK)
    wf["decision"] = {"decision": ATTACK, "comment": ATTACK}

    _, body = _get(client, "decision", http, _ok(wf, logged=True))

    _assert_escaped(body)


def test_the_admission_id_echoed_by_the_start_piece_is_escaped(client, http):
    http.return_value = _resp(200, {"status": "none"})

    resp = client.get("/ai/discharge-review", query_string={"admission_id": ATTACK, "role": "doctor"})

    body = resp.get_data(as_text=True)
    assert "discharge-review--start" in body
    _assert_escaped(body)


# ==========================================================================
# 7. /ai/care-tasks
# ==========================================================================
def test_care_tasks_sends_a_whole_number_and_shows_the_open_tasks_escaped(client, http):
    http.return_value = _resp(
        200,
        {
            "status": "ok",
            "result": {
                "admission_id": 12,
                "count": 2,
                "tasks": [
                    {"task_id": 7, "description": "2-hourly obs", "status": "pending",
                     "due_at": "2026-09-10T09:00:00"},
                    {"task_id": 8, "description": ATTACK, "status": "acknowledged", "due_at": None},
                    # id and due time are not type-checked, so they must be escaped too
                    {"task_id": ATTACK, "description": "Check drain", "status": "pending",
                     "due_at": ATTACK},
                ],
            },
        },
    )

    resp = client.post("/ai/care-tasks?role=doctor", data={"admission_id": " 12 "})

    assert resp.status_code == 200
    args, kwargs = http.call_args
    assert args[0] == "POST"
    assert args[1].endswith("/api/mcp/call")
    assert kwargs["json"] == {"tool": "homs_open_care_tasks", "arguments": {"admission_id": 12}}
    assert kwargs["headers"]["X-User-Role"] == "doctor"
    assert kwargs["timeout"] == frontend_app.CARE_TASKS_TIMEOUT

    body = resp.get_data(as_text=True)
    assert "mcp-result--ok" in body
    assert "#7" in body and "2-hourly obs" in body
    assert "2026-09-10 09:00" in body
    assert "#8" in body and "acknowledged" in body
    assert "—" in body  # no due time
    _assert_escaped(body)


def test_care_tasks_with_no_backend_reply_is_an_error_banner(client, http):
    http.side_effect = frontend_app.requests.ConnectionError("connection refused by 10.0.0.9")

    resp = client.post("/ai/care-tasks?role=doctor", data={"admission_id": "12"})

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "mcp-result--error" in body
    assert "Care task lookup failed, try again" in body
    assert "10.0.0.9" not in body
    assert "<table" not in body


# ==========================================================================
# 7. /ai/policy-question
# ==========================================================================
def test_policy_question_forwards_the_form_and_shows_a_grounded_answer(client, http):
    http.return_value = _resp(
        200,
        {
            "status": "answered",
            "answer": ATTACK,
            "sources": ["discharge-policy.md", ATTACK, 7],
            "confidence": "medium" + ATTACK,  # only checked to be a string
            "score": 0.62,
            "logged": True,
            "summary_id": 31,
        },
    )

    resp = client.post(
        "/ai/policy-question?role=doctor",
        data={"question": "When can a patient leave?", "admission_id": "12", "patient_id": ""},
    )

    assert resp.status_code == 200
    args, kwargs = http.call_args
    assert args[1].endswith("/api/rag/ask")
    # whole numbers, and the blank patient_id is left out rather than sent as ""
    assert kwargs["json"] == {"question": "When can a patient leave?", "admission_id": 12}
    assert kwargs["timeout"] == frontend_app.POLICY_QUESTION_TIMEOUT

    body = resp.get_data(as_text=True)
    assert "rag-answer--ok" in body
    assert "AI-generated" in body
    assert "Advisory only." in body
    assert "discharge-policy.md" in body
    assert "Medium" in body and "0.62" in body
    assert "<span class=\"rag-source u-mono\">7</span>" not in body  # non-text source dropped
    _assert_escaped(body)


def test_policy_question_with_nothing_approved_shows_no_answer(client, http):
    http.return_value = _resp(
        200,
        {
            "status": "insufficient_context",
            "answer": "Some model guess that must not be shown.",
            "sources": [],
            "confidence": "low",
            "score": 0.12,
        },
    )

    resp = client.post("/ai/policy-question?role=doctor", data={"question": "Parking rules?"})

    assert resp.status_code == 200
    assert http.call_args.kwargs["json"] == {"question": "Parking rules?"}

    body = resp.get_data(as_text=True)
    assert "rag-answer--insufficient" in body
    assert "Insufficient context: no approved policy covers this question" in body
    assert "Some model guess" not in body
    assert "Advisory only." not in body
    assert "AI-generated" not in body
    assert "0.12" not in body
