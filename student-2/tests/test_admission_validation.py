"""
test_admission_validation.py - unit tests for backend/services/admission_validation.py

Both public functions - require_active_for_create() and cancel_if_inactive() -
are tested in isolation. The single outbound dependency is the HTTP GET to the
Patient & Admission API, made via `requests.get` inside _fetch_admission_status.
Every test patches `admission_validation.requests.get`, so:

  * no network call is ever made and the service need not be running;
  * we can drive every branch: each admission_status value, a non-2xx
    response, a non-JSON body, a missing field, a connection error and a
    timeout.

A tiny _FakeResponse stands in for requests.Response (only the attributes the
code touches: .ok, .status_code, .json(), .text).

Scenarios (>= 1 test per listed edge case, plus branch variations):
  1. Active admission allows creation           -> require_active_for_create returns None.
  2. Inactive admission blocks creation         -> AdmissionNotActiveError
     (parametrized over Pending / Cancelled / Completed / unknown).
  3. Active admission -> no auto-cancel          -> cancel_if_inactive() is False.
  4. Inactive admission -> auto-cancel           -> cancel_if_inactive() is True
     (parametrized over the same non-active values).
  5. API failure / timeout                       -> AdmissionServiceError from
     BOTH functions, for: connection error, timeout, HTTP 500, HTTP 404,
     non-JSON body, JSON without admission_status, and empty-string status.
  6. Extra checks: the correct URL/timeout are used; 'active' lowercase is NOT
     treated as active (case-sensitive); the error carries status_code.
  7. USE_EXTERNAL_STUBS master switch: on -> every admission is Active and no
     request is made; off/unset -> exactly the behaviour above; the value is
     parsed by external_services._use_stubs() (so " TRUE " is on, "1" is not).
"""

import os
import sys

import pytest
import requests

# --- Make the backend package importable -----------------------------------
BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "backend")
)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services import admission_validation as av  # noqa: E402
from services.admission_validation import (  # noqa: E402
    AdmissionNotActiveError,
    AdmissionServiceError,
    cancel_if_inactive,
    require_active_for_create,
)


# ------------------------------------------------------------
# Test double for requests.Response.
# ------------------------------------------------------------
class _FakeResponse:
    """Minimal stand-in exposing only what _fetch_admission_status reads."""

    def __init__(self, *, json_body=None, status_code=200, text="", raise_on_json=False):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text
        self._json_body = json_body
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            # requests raises ValueError (json.JSONDecodeError subclasses it).
            raise ValueError("no JSON object could be decoded")
        return self._json_body


@pytest.fixture(autouse=True)
def stubs_switch_unset(monkeypatch):
    """
    Start every test with USE_EXTERNAL_STUBS unset, so a CI or container
    environment that turns the master switch on cannot short-circuit the
    HTTP-path tests below.
    """
    monkeypatch.delenv("USE_EXTERNAL_STUBS", raising=False)


@pytest.fixture
def mock_get(monkeypatch):
    """
    Patch admission_validation.requests.get with a MagicMock. Tests set
    mock_get.return_value (a _FakeResponse) or mock_get.side_effect (an
    exception) to shape the API's reply.
    """
    from unittest.mock import MagicMock

    m = MagicMock(name="requests.get")
    monkeypatch.setattr(av.requests, "get", m)
    return m


def _ok(status_string):
    """A 200 response whose JSON body reports the given admission_status."""
    return _FakeResponse(json_body={"admission_id": 1, "admission_status": status_string})


# The four documented non-active statuses, plus an undocumented one, to prove
# the code keys off "== 'Active'" and treats everything else as not active.
NON_ACTIVE_STATUSES = ["Pending", "Cancelled", "Completed", "Discharged", "active"]


# ==========================================================================
# 1. Active admission allows creation.
# ==========================================================================
def test_require_active_for_create_passes_when_active(mock_get):
    """
    _fetch_admission_status returns 'Active', so require_active_for_create must
    complete silently (return None) and not raise anything.
    """
    mock_get.return_value = _ok("Active")

    assert require_active_for_create(123) is None
    mock_get.assert_called_once()


def test_require_active_for_create_uses_correct_url_and_timeout(mock_get):
    """
    Branch check on the happy path: the helper must GET
    <BASE_URL>/admissions/<id> with the module's TIMEOUT, once.
    """
    mock_get.return_value = _ok("Active")

    require_active_for_create(777)

    called_url = mock_get.call_args.args[0]
    assert called_url == f"{av.BASE_URL}/admissions/777"
    assert mock_get.call_args.kwargs["timeout"] == av.TIMEOUT


# ==========================================================================
# 2. Inactive admission blocks creation.
# ==========================================================================
@pytest.mark.parametrize("status_string", NON_ACTIVE_STATUSES)
def test_require_active_for_create_raises_when_not_active(mock_get, status_string):
    """
    For every non-'Active' status (including lowercase 'active' and an
    undocumented value), require_active_for_create must raise
    AdmissionNotActiveError, and the error must carry the admission id and the
    exact status string the API reported.
    """
    mock_get.return_value = _ok(status_string)

    with pytest.raises(AdmissionNotActiveError) as excinfo:
        require_active_for_create(42)

    assert excinfo.value.admission_id == 42
    assert excinfo.value.admission_status == status_string


# ==========================================================================
# 3. Active admission -> no automatic cancelling.
# ==========================================================================
def test_cancel_if_inactive_returns_false_when_active(mock_get):
    """
    'Active' -> cancel_if_inactive must return False (falsy), telling the caller
    to leave the open request alone. Asserted as `is False`, not just falsy, so
    a None regression would fail.
    """
    mock_get.return_value = _ok("Active")

    assert cancel_if_inactive(123) is False


# ==========================================================================
# 4. Inactive admission -> auto-cancel is triggered.
# ==========================================================================
@pytest.mark.parametrize("status_string", NON_ACTIVE_STATUSES)
def test_cancel_if_inactive_returns_true_when_not_active(mock_get, status_string):
    """
    For every non-'Active' status, cancel_if_inactive must return True so the
    caller auto-cancels the request. Parametrized to prove it is "not Active",
    not an explicit list of cancel-worthy statuses.
    """
    mock_get.return_value = _ok(status_string)

    assert cancel_if_inactive(99) is True


# ==========================================================================
# 5. The Patient & Admission API call fails or times out.
# ==========================================================================
# Each entry is a way the underlying requests.get / response can go wrong.
# id -> (how to break it: "raise" an exception, or "return" a bad response)
_FAILURE_MODES = {
    "connection_error": ("raise", requests.ConnectionError("connection refused")),
    "timeout": ("raise", requests.Timeout("read timed out")),
    "generic_request_exception": ("raise", requests.RequestException("boom")),
    "http_500": ("return", _FakeResponse(status_code=500, text="server error")),
    "http_404": ("return", _FakeResponse(status_code=404, text="not found")),
    "http_401": ("return", _FakeResponse(status_code=401, text="unauthorized")),
    "non_json_body": ("return", _FakeResponse(raise_on_json=True, text="<html>oops</html>")),
    "json_without_status": ("return", _FakeResponse(json_body={"admission_id": 1})),
    "empty_status_string": ("return", _FakeResponse(json_body={"admission_status": ""})),
    "null_status": ("return", _FakeResponse(json_body={"admission_status": None})),
}


@pytest.mark.parametrize("mode", list(_FAILURE_MODES), ids=list(_FAILURE_MODES))
@pytest.mark.parametrize(
    "func", [require_active_for_create, cancel_if_inactive], ids=["require", "cancel"]
)
def test_api_failure_raises_admission_service_error(mock_get, mode, func):
    """
    Whatever the failure mode - transport exception, timeout, non-2xx status,
    unparseable body, or a body missing/blanking admission_status - BOTH public
    functions must surface it as AdmissionServiceError and never as a bare
    requests error, a KeyError, or a wrong bool/None.
    """
    kind, thing = _FAILURE_MODES[mode]
    if kind == "raise":
        mock_get.side_effect = thing
    else:
        mock_get.return_value = thing

    with pytest.raises(AdmissionServiceError):
        func(123)


@pytest.mark.parametrize("http_status", [400, 404, 500, 503])
def test_admission_service_error_from_http_carries_status_code(mock_get, http_status):
    """
    When the failure is an HTTP error response, the raised AdmissionServiceError
    must record the numeric status_code (callers may branch on it). Transport
    failures, by contrast, leave status_code as None - checked separately below.
    """
    mock_get.return_value = _FakeResponse(status_code=http_status, text="err")

    with pytest.raises(AdmissionServiceError) as excinfo:
        require_active_for_create(1)
    assert excinfo.value.status_code == http_status


def test_admission_service_error_from_transport_has_no_status_code(mock_get):
    """
    A connection error / timeout never produced an HTTP response, so
    status_code must be None (not 0, not a stray int).
    """
    mock_get.side_effect = requests.Timeout("read timed out")

    with pytest.raises(AdmissionServiceError) as excinfo:
        cancel_if_inactive(1)
    assert excinfo.value.status_code is None


# ==========================================================================
# 6. Boundary detail: only the exact string 'Active' counts.
# ==========================================================================
@pytest.mark.parametrize("weird", ["Active ", " Active", "ACTIVE", "Activated"])
def test_status_match_is_exact_and_case_sensitive(mock_get, weird):
    """
    The code compares `status != ACTIVE_STATUS` with no trimming or casefolding.
    A near-miss string must therefore be treated as NOT active: creation blocked,
    auto-cancel triggered.
    """
    mock_get.return_value = _ok(weird)

    with pytest.raises(AdmissionNotActiveError):
        require_active_for_create(5)

    mock_get.return_value = _ok(weird)
    assert cancel_if_inactive(5) is True


def test_admission_id_is_interpolated_into_the_url(mock_get):
    """
    Confirms the admission_id argument actually reaches the request URL, for
    both functions, so a caller can't silently query the wrong admission.
    """
    mock_get.return_value = _ok("Active")
    require_active_for_create("abc")
    assert mock_get.call_args.args[0].endswith("/admissions/abc")

    mock_get.return_value = _ok("Active")
    cancel_if_inactive(2024)
    assert mock_get.call_args.args[0].endswith("/admissions/2024")


# ==========================================================================
# 7. USE_EXTERNAL_STUBS master switch.
# ==========================================================================
def test_switch_on_makes_no_request_and_counts_as_active(monkeypatch, mock_get):
    """
    With USE_EXTERNAL_STUBS=true the shared helper must answer 'Active' itself:
    creation is allowed, no auto-cancel is asked for, and requests.get is never
    called - even though it is rigged to fail if it were.
    """
    monkeypatch.setenv("USE_EXTERNAL_STUBS", "true")
    mock_get.side_effect = requests.ConnectionError("service not running")

    assert av._fetch_admission_status(123) == "Active"
    assert require_active_for_create(123) is None
    assert cancel_if_inactive(123) is False
    mock_get.assert_not_called()


def test_switch_is_read_from_the_existing_external_services_function(monkeypatch, mock_get):
    """
    admission_validation must reuse external_services._use_stubs() rather than
    parse the environment itself: forcing that function to True (with the
    variable unset) is enough to bypass the HTTP call.
    """
    monkeypatch.setattr(av.external_services, "_use_stubs", lambda: True)

    assert require_active_for_create(5) is None
    mock_get.assert_not_called()


@pytest.mark.parametrize("value", [None, "false", "", "0"])
def test_switch_off_behaves_as_before(monkeypatch, mock_get, value):
    """
    Unset, 'false', empty and '0' all mean off: the HTTP request is made with
    the usual URL and timeout, and a non-active status still blocks creation.
    """
    if value is not None:
        monkeypatch.setenv("USE_EXTERNAL_STUBS", value)
    mock_get.return_value = _ok("Pending")

    with pytest.raises(AdmissionNotActiveError) as excinfo:
        require_active_for_create(42)

    assert excinfo.value.admission_status == "Pending"
    mock_get.assert_called_once()
    assert mock_get.call_args.args[0] == f"{av.BASE_URL}/admissions/42"
    assert mock_get.call_args.kwargs["timeout"] == av.TIMEOUT


def test_switch_off_still_surfaces_api_failure(monkeypatch, mock_get):
    """Off means the unreachable-service error is raised exactly as today."""
    monkeypatch.setenv("USE_EXTERNAL_STUBS", "false")
    mock_get.side_effect = requests.Timeout("read timed out")

    with pytest.raises(AdmissionServiceError):
        cancel_if_inactive(1)


@pytest.mark.parametrize("value", [" TRUE ", "True", "TRUE", "\ttrue\n"])
def test_switch_value_is_trimmed_and_case_insensitive(monkeypatch, mock_get, value):
    """
    The existing switch strips whitespace and lower-cases before comparing to
    'true', so ' TRUE ' is ON here too: no request, admission is Active.
    """
    monkeypatch.setenv("USE_EXTERNAL_STUBS", value)

    assert cancel_if_inactive(7) is False
    mock_get.assert_not_called()


@pytest.mark.parametrize("value", ["1", "yes", "on", "truee"])
def test_switch_only_accepts_true_like_the_existing_switch(monkeypatch, mock_get, value):
    """
    The existing switch is stricter than the MCP/RAG flags: only 'true' counts.
    '1', 'yes' and 'on' are OFF, so the normal HTTP path runs.
    """
    monkeypatch.setenv("USE_EXTERNAL_STUBS", value)
    mock_get.return_value = _ok("Active")

    assert require_active_for_create(8) is None
    mock_get.assert_called_once()
