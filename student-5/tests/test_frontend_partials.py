"""Tests for the Student 5 frontend microservice (Workforce Overview).

Staff & Shift Management — HTMX partial rendering. Stubs api_client so these
run without a live backend/database process.

The frontend's app.py is loaded via importlib under a private module name
rather than a bare `import app`, so it never touches ``sys.modules["app"]`` —
the backend suite in this same directory does `from app import create_app`
relying on a bare "app" resolved from BACKEND_DIR on sys.path (see
conftest.py). Keeping the two "app.py" files out of each other's way avoids
cross-test contamination regardless of collection order.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

import pytest

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
if str(FRONTEND_DIR) not in sys.path:
    sys.path.append(str(FRONTEND_DIR))


def _load_frontend_app_module():
    spec = importlib.util.spec_from_file_location(
        "student5_frontend_app", FRONTEND_DIR / "app.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fe_api_client():
    """The api_client module the frontend imports — monkeypatched per test."""
    import api_client
    return api_client


def _enter_demo_as(test_client, role, staff_id=None, name=None):
    """Establish the simulated R0 demo identity on a test client.

    Written directly into the session rather than by posting the entry form,
    so a test that is about the shift planner is not also a test of the demo
    screen. Tests that care about the entry screen exercise it properly.
    """
    import demo_identity
    with test_client.session_transaction() as session:
        session[demo_identity.SESSION_KEY] = {
            "role": role, "staff_id": staff_id, "name": name}


@pytest.fixture
def frontend_app():
    """The frontend Flask app, with no demo identity established."""
    frontend_app_module = _load_frontend_app_module()
    app = frontend_app_module.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test-key")
    return app


@pytest.fixture
def anonymous_client(frontend_app):
    """A client that has NOT chosen a demo role — sees the entry gate."""
    with frontend_app.test_client() as test_client:
        yield test_client


@pytest.fixture
def frontend_client(fe_api_client, frontend_app):
    """The default client: a Staff Manager, which is what every test written
    before role separation existed implicitly assumed."""
    with frontend_app.test_client() as test_client:
        _enter_demo_as(test_client, "Staff Manager", name="Demo Staff Manager")
        yield test_client


@pytest.fixture
def employee_client(fe_api_client, frontend_app):
    """A client acting as employee 1."""
    with frontend_app.test_client() as test_client:
        _enter_demo_as(test_client, "Employee", staff_id=1, name="Amara Okafor")
        yield test_client


UNAVAILABLE_MESSAGE = (
    "Workforce data is temporarily unavailable. "
    "Check that the Staff & Shift service is running."
)
# Jinja HTML-escapes the message when it renders `{{ error }}`; compare
# against that escaped form since these tests assert on raw response bytes.
UNAVAILABLE_MESSAGE_HTML = UNAVAILABLE_MESSAGE.replace("&", "&amp;")


def _raise_unavailable(*args, **kwargs):
    import api_client
    raise api_client.BackendUnavailableError(UNAVAILABLE_MESSAGE)


# ------------------------------------------------------------------- shell
def test_index_renders_shell_without_calling_backend(frontend_client, fe_api_client, monkeypatch):
    """The page shell must render even if every backend call would fail —
    it makes none on the initial GET /."""
    monkeypatch.setattr(fe_api_client, "get_coverage", _raise_unavailable)
    monkeypatch.setattr(fe_api_client, "list_staff", _raise_unavailable)
    monkeypatch.setattr(fe_api_client, "get_coverage_summary", _raise_unavailable)

    response = frontend_client.get("/")

    assert response.status_code == 200
    assert b"Workforce overview" in response.data
    assert b"kpi-row" in response.data
    assert b"demand-panel" in response.data
    assert b"summary-panel" in response.data


# -------------------------------------------------------------------- kpis
def test_kpis_partial_renders_real_data(frontend_client, fe_api_client, monkeypatch):
    def fake_get_coverage(shift_date=None, department=None):
        return {
            "summary": {"total_shifts": 2, "fully_staffed": 2, "understaffed": 0,
                        "unstaffed": 0, "total_shortfall": 0},
            "shifts": [
                {"shift_id": 1, "department": "Pharmacy", "required_staff_count": 1,
                 "assigned_staff_count": 1, "shortfall": 0, "coverage_status": "Fully staffed",
                 "start_time": "08:00", "end_time": "16:00", "required_role": "Pharmacist"},
                {"shift_id": 2, "department": "Rehabilitation", "required_staff_count": 1,
                 "assigned_staff_count": 1, "shortfall": 0, "coverage_status": "Fully staffed",
                 "start_time": "09:00", "end_time": "17:00", "required_role": "Physiotherapist"},
            ],
        }

    def fake_list_staff(availability_status=None, department=None):
        return {"count": 10, "staff": []} if availability_status == "Available" \
            else {"count": 12, "staff": []}

    monkeypatch.setattr(fe_api_client, "get_coverage", fake_get_coverage)
    monkeypatch.setattr(fe_api_client, "list_staff", fake_list_staff)

    response = frontend_client.get("/partials/kpis?date=2026-08-26")
    body = response.data.decode()

    assert response.status_code == 200
    assert "100%" in body        # coverage: 2/2 assigned
    assert ">0<" in body or ">0 <" in body  # unfilled positions
    assert ">12<" in body        # total roster
    assert ">10<" in body        # available now
    assert ">2<" in body         # shifts today
    assert "undefined" not in body and "NaN" not in body


def test_kpis_partial_handles_no_shifts_today(frontend_client, fe_api_client, monkeypatch):
    """required_staff_count totals to zero -> coverage_pct must be None (—),
    never a ZeroDivisionError or NaN."""
    def fake_get_coverage(shift_date=None, department=None):
        return {"summary": {"total_shifts": 0, "fully_staffed": 0, "understaffed": 0,
                             "unstaffed": 0, "total_shortfall": 0}, "shifts": []}

    monkeypatch.setattr(fe_api_client, "get_coverage", fake_get_coverage)
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda availability_status=None, department=None: {"count": 0, "staff": []})

    response = frontend_client.get("/partials/kpis")
    body = response.data.decode()

    assert response.status_code == 200
    assert "—" in body
    assert "NaN" not in body and "undefined" not in body


def test_kpis_partial_backend_unavailable(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage", _raise_unavailable)

    response = frontend_client.get("/partials/kpis")
    body = response.data.decode()

    assert response.status_code == 200   # section-scoped error, not a 500
    assert UNAVAILABLE_MESSAGE_HTML in body
    assert "Workforce data unavailable" in body


# ------------------------------------------------------------------ demand
def test_demand_partial_empty_state(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage",
                        lambda shift_date=None, department=None:
                            {"summary": {"total_shifts": 0}, "shifts": []})

    response = frontend_client.get("/partials/demand?date=2030-01-01")
    body = response.data.decode()

    assert response.status_code == 200
    assert "No shifts scheduled" in body


def test_demand_partial_flags_top_gap(frontend_client, fe_api_client, monkeypatch):
    def fake_get_coverage(shift_date=None, department=None):
        return {
            "summary": {"total_shifts": 1},
            "shifts": [{
                "shift_id": 9, "department": "Radiology", "required_staff_count": 2,
                "assigned_staff_count": 0, "shortfall": 2, "coverage_status": "Unstaffed",
                "start_time": "23:00", "end_time": "07:00", "required_role": "Radiographer",
            }],
        }

    monkeypatch.setattr(fe_api_client, "get_coverage", fake_get_coverage)

    response = frontend_client.get("/partials/demand")
    body = response.data.decode()

    assert response.status_code == 200
    assert "Radiology needs attention" in body
    assert "badge-danger" in body   # Unstaffed maps to the danger badge


def test_demand_partial_unavailable(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage", _raise_unavailable)

    response = frontend_client.get("/partials/demand")

    assert response.status_code == 200
    assert UNAVAILABLE_MESSAGE_HTML in response.data.decode()


# ----------------------------------------------------------------- summary
def test_summary_partial_renders_headline(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage_summary",
                        lambda shift_date=None, department=None: {
                            "headline": "1 of 1 shift(s) are short by 2 staff member(s) in total.",
                            "gaps": [{"department": "Radiology", "required_role": "Radiographer",
                                      "shortfall": 2}],
                        })

    response = frontend_client.get("/partials/summary")
    body = response.data.decode()

    assert response.status_code == 200
    assert "short by 2 staff member" in body
    assert "Rule-based summary" in body
    assert "AI-generated" not in body or "not AI-generated" in body


def test_summary_partial_unavailable(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage_summary", _raise_unavailable)

    response = frontend_client.get("/partials/summary")

    assert response.status_code == 200
    assert UNAVAILABLE_MESSAGE_HTML in response.data.decode()


# ------------------------------------------------------------------ health
def test_health_reports_degraded_when_backend_down(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_health", _raise_unavailable)

    response = frontend_client.get("/health")

    assert response.status_code == 503
    assert response.get_json()["status"] == "degraded"


def test_health_reports_ok_when_backend_up(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_health", lambda: {"status": "ok"})

    response = frontend_client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


# --------------------------------------------------------- staff directory
STAFF_FIXTURE = [
    {"staff_id": 1, "name": "Amara Okafor", "role": "Registered Nurse",
     "department": "Emergency", "specialisation": "Triage",
     "availability_status": "Available", "employment_status": "Full-Time"},
    {"staff_id": 2, "name": "Mei Lin Tan", "role": "Doctor",
     "department": "Surgery", "specialisation": None,
     "availability_status": "On Leave", "employment_status": "Full-Time"},
]


def test_staff_directory_shell_renders(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: {"count": 2, "staff": STAFF_FIXTURE})
    response = frontend_client.get("/staff")
    body = response.data.decode()
    assert response.status_code == 200
    assert "Staff directory" in body
    assert "staff-results" in body
    # Filter options are derived from real records, not hard-coded.
    assert "Emergency" in body and "Surgery" in body


def test_staff_directory_shell_survives_backend_failure(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff", _raise_unavailable)
    response = frontend_client.get("/staff")
    assert response.status_code == 200
    assert "Staff directory" in response.data.decode()


def test_staff_table_renders_records(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: {"count": 2, "staff": STAFF_FIXTURE})
    body = frontend_client.get("/partials/staff-table").data.decode()
    assert "Amara Okafor" in body and "Mei Lin Tan" in body
    assert "badge-success" in body   # Available
    assert "badge-neutral" in body   # On Leave
    assert "undefined" not in body and "None" not in body


def test_staff_table_uses_search_endpoint_when_query_given(frontend_client, fe_api_client, monkeypatch):
    called = {}

    def fake_search(**kwargs):
        called.update(kwargs)
        return {"count": 1, "staff": STAFF_FIXTURE[:1]}

    monkeypatch.setattr(fe_api_client, "search_staff", fake_search)
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("should not be called")))

    body = frontend_client.get("/partials/staff-table?q=nurse").data.decode()
    assert called["query"] == "nurse"
    assert "Amara Okafor" in body


def test_staff_table_blank_query_uses_list_endpoint(frontend_client, fe_api_client, monkeypatch):
    """A blank q must not reach /api/staff/search, which rejects it."""
    monkeypatch.setattr(fe_api_client, "search_staff",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("should not be called")))
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: {"count": 2, "staff": STAFF_FIXTURE})
    response = frontend_client.get("/partials/staff-table?q=%20%20")
    assert response.status_code == 200


def test_staff_table_no_results_state(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "search_staff", lambda **kw: {"count": 0, "staff": []})
    body = frontend_client.get("/partials/staff-table?q=zzz").data.decode()
    assert "No staff match the current search and filters" in body


def test_staff_table_empty_dataset_state(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **kw: {"count": 0, "staff": []})
    body = frontend_client.get("/partials/staff-table").data.decode()
    assert "No staff records" in body


def test_staff_table_backend_unavailable(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff", _raise_unavailable)
    response = frontend_client.get("/partials/staff-table")
    assert response.status_code == 200
    assert "Staff records unavailable" in response.data.decode()


# ------------------------------------------- staff detail & availability
def test_staff_table_employment_filter_uses_list_endpoint(frontend_client, fe_api_client, monkeypatch):
    called = {}

    def fake_list(**kwargs):
        called.update(kwargs)
        return {"count": 1, "staff": STAFF_FIXTURE[:1]}

    monkeypatch.setattr(fe_api_client, "list_staff", fake_list)
    frontend_client.get("/partials/staff-table?employment_status=Full-Time")
    assert called["employment_status"] == "Full-Time"


def test_staff_table_combines_all_filters_with_search(frontend_client, fe_api_client, monkeypatch):
    called = {}

    def fake_search(**kwargs):
        called.update(kwargs)
        return {"count": 0, "staff": []}

    monkeypatch.setattr(fe_api_client, "search_staff", fake_search)
    frontend_client.get("/partials/staff-table"
                        "?q=nurse&department=Emergency&role=Doctor"
                        "&availability_status=Available&employment_status=Casual")
    assert called == {"query": "nurse", "department": "Emergency", "role": "Doctor",
                      "availability_status": "Available", "employment_status": "Casual"}


def test_staff_detail_renders_real_fields(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff",
                        lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "Amara Okafor" in body
    assert "Triage" in body            # specialisation exists -> shown
    # Staff ID now sits in the drawer header as the display identifier.
    assert "S-001" in body


def test_staff_detail_omits_absent_specialisation(frontend_client, fe_api_client, monkeypatch):
    """A null specialisation must be omitted, never fabricated or shown as None."""
    monkeypatch.setattr(fe_api_client, "get_staff",
                        lambda sid: {"staff": STAFF_FIXTURE[1]})
    body = frontend_client.get("/partials/staff/2").data.decode()
    assert "Specialisation" not in body
    assert "None" not in body


def test_staff_detail_backend_unavailable(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff", _raise_unavailable)
    response = frontend_client.get("/partials/staff/1")
    assert response.status_code == 200
    assert "Staff record unavailable" in response.data.decode()


def test_availability_update_success(frontend_client, fe_api_client, monkeypatch):
    updated = {**STAFF_FIXTURE[0], "availability_status": "On Leave"}
    monkeypatch.setattr(fe_api_client, "update_availability",
                        lambda sid, status: {"staff": updated})
    # A successful mutation is followed by the normal real record reload.
    # Stub both API boundaries so this unit test never leaks to localhost.
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": updated})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    response = frontend_client.post("/partials/staff/1/availability",
                                    data={"availability_status": "On Leave"})
    assert response.status_code == 200
    assert "Availability updated to On Leave." in response.data.decode()


def test_availability_update_rejects_invalid_status(frontend_client, fe_api_client, monkeypatch):
    """An invalid value must never reach the backend."""
    monkeypatch.setattr(fe_api_client, "get_staff",
                        lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "update_availability",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    response = frontend_client.post("/partials/staff/1/availability",
                                    data={"availability_status": "Bogus"})
    assert response.status_code == 400
    assert "Select a valid availability status." in response.data.decode()


def test_availability_update_failure_does_not_claim_success(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "update_availability", _raise_unavailable)
    monkeypatch.setattr(fe_api_client, "get_staff",
                        lambda sid: {"staff": STAFF_FIXTURE[0]})
    body = frontend_client.post("/partials/staff/1/availability",
                                data={"availability_status": "On Leave"}).data.decode()
    assert "Availability was not updated." in body
    assert "Availability updated to" not in body


# ------------------------------------------------ drawer (iteration 3)
def _shift(**kw):
    base = {"shift_id": 1, "department": "Emergency", "shift_date": "2026-08-27",
            "start_time": "07:00", "end_time": "15:00", "shift_status": "Planned",
            "assignment_id": 1, "assignment_status": "Assigned"}
    base.update(kw)
    return base


def test_drawer_shows_deterministic_display_id(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "S-001" in body
    # The mockup's HR-style identifiers must never appear.
    assert "EMP-" not in body


def test_drawer_omits_absent_specialisation_and_notes(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[1]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    body = frontend_client.get("/partials/staff/2").data.decode()
    assert "Specialisation" not in body
    assert "Operational notes" not in body
    assert "None" not in body


def test_drawer_contains_no_fabricated_mockup_fields(frontend_client, fe_api_client, monkeypatch):
    """Guards against reintroducing mockup-only concepts the schema lacks."""
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    body = frontend_client.get("/partials/staff/1").data.decode().lower()
    # "weekly availability" was forbidden while the schema could not support
    # it; it is now a real persisted feature, so the guard targets the mockup
    # concepts that remain unbacked by data.
    for forbidden in ("qualification", "bank contract", "band 5", "band 6",
                      "confidence", "suitability",
                      "shift short", "open gaps", "matches 3"):
        assert forbidden not in body, forbidden


def test_drawer_renders_upcoming_shifts(frontend_client, fe_api_client, monkeypatch):
    import datetime
    soon = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts",
                        lambda sid: {"shifts": [_shift(shift_date=soon)]})
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert soon in body
    assert "Emergency" in body


def test_drawer_excludes_cancelled_assignments(frontend_client, fe_api_client, monkeypatch):
    import datetime
    soon = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts",
                        lambda sid: {"shifts": [_shift(shift_date=soon,
                                                        assignment_status="Cancelled")]})
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "No assigned shifts in the next seven days." in body


def test_drawer_no_active_assignment_state(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "No active assignment" in body


def test_drawer_survives_shift_lookup_failure(frontend_client, fe_api_client, monkeypatch):
    """A shift-service failure must not take the whole drawer down."""
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", _raise_unavailable)
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "Amara Okafor" in body                 # record still rendered
    assert "Assignment data is unavailable" in body


def test_drawer_staff_not_found(frontend_client, fe_api_client, monkeypatch):
    import api_client as ac

    def raise_nf(sid):
        raise ac.NotFoundError("Staff 999 not found.")

    monkeypatch.setattr(fe_api_client, "get_staff", raise_nf)
    response = frontend_client.get("/partials/staff/999")
    assert response.status_code == 404
    assert "Staff member not found" in response.data.decode()


def test_availability_update_emits_table_refresh_trigger(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "update_availability",
                        lambda sid, s: {"staff": STAFF_FIXTURE[0]})
    response = frontend_client.post("/partials/staff/1/availability",
                                    data={"availability_status": "On Leave"})
    assert response.headers.get("HX-Trigger") == "staff-updated"


def test_failed_availability_update_emits_no_refresh_trigger(frontend_client, fe_api_client, monkeypatch):
    """A failed save must not signal the table that anything changed."""
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "update_availability", _raise_unavailable)
    response = frontend_client.post("/partials/staff/1/availability",
                                    data={"availability_status": "On Leave"})
    assert "HX-Trigger" not in response.headers
    assert "Availability was not updated." in response.data.decode()


def test_availability_rejects_roster_state_values(frontend_client, fe_api_client, monkeypatch):
    """'On shift'/'Off duty' are roster concepts, not availability values."""
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    for bad in ("On shift", "Off duty"):
        response = frontend_client.post("/partials/staff/1/availability",
                                        data={"availability_status": bad})
        assert response.status_code == 400


def test_staff_table_shows_display_id_and_initials(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: {"count": 2, "staff": STAFF_FIXTURE})
    body = frontend_client.get("/partials/staff-table").data.decode()
    assert "S-001" in body
    assert ">AO<" in body      # initials from the real name
    assert "Specialisation" in body


# ---------------------------------------------------------- shift planner
SHIFT_FIXTURE = {
    "shift_id": 11, "department": "Emergency", "shift_date": "2026-08-27",
    "start_time": "23:00", "end_time": "07:00",
    "required_role": "Registered Nurse", "required_staff_count": 2,
    "shift_status": "Planned", "notes": "Night shift.",
}

COVERAGE_FIXTURE = {
    "filters": {},
    "summary": {"total_shifts": 1, "fully_staffed": 0, "understaffed": 1,
                "unstaffed": 0, "total_shortfall": 1},
    "shifts": [{
        "shift_id": 11, "department": "Emergency", "shift_date": "2026-08-27",
        "start_time": "23:00", "end_time": "07:00",
        "required_role": "Registered Nurse", "required_staff_count": 2,
        "assigned_staff_count": 1, "shortfall": 1,
        "coverage_status": "Understaffed",
    }],
}

SHIFT_FORM_STAFF = [
    {"staff_id": 1, "role": "Registered Nurse", "department": "Emergency"},
    {"staff_id": 2, "role": "Doctor", "department": "General Ward"},
    {"staff_id": 3, "role": "Registered Nurse", "department": "Emergency"},
    {"staff_id": 4, "role": "Pharmacist", "department": "Pharmacy"},
]


def _stub_shift_form_options(monkeypatch, fe_api_client):
    monkeypatch.setattr(
        fe_api_client, "list_staff",
        lambda **kwargs: {"count": len(SHIFT_FORM_STAFF), "staff": SHIFT_FORM_STAFF})


def _stub_shift_detail(monkeypatch, fe_api_client, assignments=None, candidates=None):
    monkeypatch.setattr(fe_api_client, "get_shift", lambda shift_id: {"shift": SHIFT_FIXTURE})
    monkeypatch.setattr(
        fe_api_client, "list_shift_assignments",
        lambda shift_id: {"count": len(assignments or []), "assignments": assignments or []})
    monkeypatch.setattr(
        fe_api_client, "list_staff",
        lambda **kwargs: {"count": len(candidates or []), "staff": candidates or []})


def test_shift_planner_shell_and_navigation(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts",
                        lambda **kwargs: {"count": 1, "shifts": [SHIFT_FIXTURE]})
    body = frontend_client.get("/shifts?date=2026-08-27").data.decode()
    assert "Shift planner" in body
    assert 'aria-current="page">Shift planner' in body
    assert 'id="planner-workspace"' in body
    assert 'name="week_start" value="2026-08-24"' in body
    assert 'hx-trigger="load, shifts-updated from:body"' in body


def test_shift_planner_shell_survives_backend_failure(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts", _raise_unavailable)
    response = frontend_client.get("/shifts")
    body = response.data.decode()
    assert response.status_code == 200
    assert "Shift planner" in body
    assert "Scheduling service unavailable" in body


def test_shift_list_uses_backend_date_department_and_status_filters(
        frontend_client, fe_api_client, monkeypatch):
    called = {}

    def fake_list(**kwargs):
        called.update(kwargs)
        return {"count": 1, "shifts": [SHIFT_FIXTURE]}

    monkeypatch.setattr(fe_api_client, "list_shifts", fake_list)
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **kwargs: COVERAGE_FIXTURE)
    response = frontend_client.get(
        "/partials/shifts?shift_date=2026-08-27&department=Emergency&shift_status=Planned")
    assert response.status_code == 200
    assert called == {"shift_date": "2026-08-27", "department": "Emergency",
                      "shift_status": "Planned"}
    assert "1 / 2" in response.data.decode()


def test_shift_list_required_role_filter_is_server_side(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts",
                        lambda **kwargs: {"count": 1, "shifts": [SHIFT_FIXTURE]})
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **kwargs: COVERAGE_FIXTURE)
    body = frontend_client.get(
        "/partials/shifts?shift_date=2026-08-27&required_role=Doctor").data.decode()
    assert "No shifts match the current filters" in body
    assert "Night shift." not in body


def test_shift_list_distinguishes_no_shifts_and_no_filter_matches(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts",
                        lambda **kwargs: {"count": 0, "shifts": []})
    monkeypatch.setattr(fe_api_client, "get_coverage",
                        lambda **kwargs: {"summary": {}, "shifts": []})
    empty = frontend_client.get("/partials/shifts?shift_date=2030-01-01").data.decode()
    filtered = frontend_client.get(
        "/partials/shifts?shift_date=2030-01-01&department=Emergency").data.decode()
    assert "No shifts scheduled for this date." in empty
    assert "No shifts match the current filters" in filtered


def test_shift_list_coverage_derives_overstaffed_state(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts",
                        lambda **kwargs: {"count": 1, "shifts": [SHIFT_FIXTURE]})
    coverage = {**COVERAGE_FIXTURE, "shifts": [
        {**COVERAGE_FIXTURE["shifts"][0], "assigned_staff_count": 3, "shortfall": 0,
         "coverage_status": "Overstaffed"}]}
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **kwargs: coverage)
    body = frontend_client.get("/partials/shifts?shift_date=2026-08-27").data.decode()
    assert "3 / 2" in body
    assert "Overstaffed by 1" in body


def test_shift_list_backend_unavailable_is_isolated(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts", _raise_unavailable)
    body = frontend_client.get("/partials/shifts").data.decode()
    assert "Shift planner unavailable" in body
    assert UNAVAILABLE_MESSAGE_HTML in body


def test_shift_list_database_unavailable_message_is_isolated(
        frontend_client, fe_api_client, monkeypatch):
    import api_client

    def raise_database_error(**kwargs):
        raise api_client.BackendError(
            "Staff & Shift service returned 503: Database service unreachable")

    monkeypatch.setattr(fe_api_client, "list_shifts", raise_database_error)
    body = frontend_client.get("/partials/shifts").data.decode()
    assert "Shift planner unavailable" in body
    assert "Database service unreachable" in body


def test_new_shift_form_uses_selected_date(
        frontend_client, fe_api_client, monkeypatch):
    _stub_shift_form_options(monkeypatch, fe_api_client)
    body = frontend_client.get(
        "/partials/shifts/new?shift_date=2026-08-29").data.decode()
    assert "Create shift" in body
    assert 'value="2026-08-29"' in body
    assert 'min="1"' in body


def test_shift_form_options_are_real_distinct_sorted_selects(
        frontend_client, fe_api_client, monkeypatch):
    _stub_shift_form_options(monkeypatch, fe_api_client)
    body = frontend_client.get("/partials/shifts/new").data.decode()

    assert '<select class="select" name="department" required>' in body
    assert '<select class="select" name="required_role" required>' in body
    assert 'input class="input" name="department"' not in body
    assert 'input class="input" name="required_role"' not in body
    assert body.count('value="Emergency"') == 1
    assert body.count('value="Registered Nurse"') == 1
    assert body.index('value="Emergency"') < body.index('value="General Ward"')
    assert body.index('value="General Ward"') < body.index('value="Pharmacy"')
    assert body.index('value="Doctor"') < body.index('value="Pharmacist"')
    assert body.index('value="Pharmacist"') < body.index('value="Registered Nurse"')


def test_shift_templates_are_ui_only_and_create_defaults_to_morning(
        frontend_client, fe_api_client, monkeypatch):
    _stub_shift_form_options(monkeypatch, fe_api_client)
    body = frontend_client.get("/partials/shifts/new").data.decode()

    assert 'data-shift-template' in body
    assert 'name="shift_template"' not in body
    assert 'value="Morning" selected' in body
    assert 'Morning · 07:00–15:00' in body
    assert 'Afternoon · 15:00–23:00' in body
    assert 'Night · 23:00–07:00' in body
    assert 'value="Custom"' in body


def test_shift_form_reference_failure_is_isolated_and_submit_is_disabled(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff", _raise_unavailable)
    body = frontend_client.get("/partials/shifts/new").data.decode()
    assert "Shift options are unavailable" in body
    assert UNAVAILABLE_MESSAGE_HTML in body
    assert 'type="submit" disabled' in body


def test_create_shift_success_emits_single_refresh_event(
        frontend_client, fe_api_client, monkeypatch):
    submitted = {}

    def fake_create(payload):
        submitted.update(payload)
        return {"shift": SHIFT_FIXTURE}

    monkeypatch.setattr(fe_api_client, "create_shift", fake_create)
    _stub_shift_detail(monkeypatch, fe_api_client)
    response = frontend_client.post("/partials/shifts", data={
        "department": "Emergency", "shift_date": "2026-08-27",
        "start_time": "23:00", "end_time": "07:00",
        "required_role": "Registered Nurse", "required_staff_count": "2",
        "shift_status": "Planned", "notes": "Night shift.",
    })
    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "shifts-updated"
    assert submitted["required_staff_count"] == 2
    assert "Shift created." in response.data.decode()


def test_create_shift_validation_preserves_values(
        frontend_client, fe_api_client, monkeypatch):
    _stub_shift_form_options(monkeypatch, fe_api_client)
    monkeypatch.setattr(
        fe_api_client, "create_shift",
        lambda payload: (_ for _ in ()).throw(AssertionError("must not be called")))
    body = frontend_client.post("/partials/shifts", data={
        "department": "Emergency", "shift_date": "2026-08-27",
        "start_time": "23:00", "end_time": "07:00",
        "required_role": "Registered Nurse", "required_staff_count": "0",
        "shift_status": "Planned", "notes": "Keep this note",
    }).data.decode()
    assert "greater than zero" in body
    assert "Keep this note" in body
    assert 'value="Emergency" selected' in body
    assert 'value="Registered Nurse" selected' in body


def test_create_shift_surfaces_backend_validation(
        frontend_client, fe_api_client, monkeypatch):
    import api_client
    _stub_shift_form_options(monkeypatch, fe_api_client)
    monkeypatch.setattr(
        fe_api_client, "create_shift",
        lambda payload: (_ for _ in ()).throw(
            api_client.BackendError("start_time and end_time must differ")))
    body = frontend_client.post("/partials/shifts", data={
        "department": "Emergency", "shift_date": "2026-08-27",
        "start_time": "07:00", "end_time": "07:00",
        "required_role": "Registered Nurse", "required_staff_count": "2",
        "shift_status": "Planned",
    }).data.decode()
    assert "Shift was not saved" in body
    assert "must differ" in body


def test_edit_shift_form_and_update(frontend_client, fe_api_client, monkeypatch):
    _stub_shift_form_options(monkeypatch, fe_api_client)
    monkeypatch.setattr(fe_api_client, "get_shift", lambda sid: {"shift": SHIFT_FIXTURE})
    form = frontend_client.get("/partials/shifts/11/edit").data.decode()
    assert "Edit shift" in form and "Night shift." in form
    assert 'value="Emergency" selected' in form
    assert 'value="Registered Nurse" selected' in form
    assert 'value="Planned" selected' in form
    assert 'value="Night" selected' in form
    assert "Lifecycle state of the shift" in form

    updated = {}
    monkeypatch.setattr(fe_api_client, "update_shift",
                        lambda sid, payload: updated.update(payload) or {"shift": SHIFT_FIXTURE})
    _stub_shift_detail(monkeypatch, fe_api_client)
    response = frontend_client.post("/partials/shifts/11", data={
        "department": "Emergency", "shift_date": "2026-08-27",
        "start_time": "22:00", "end_time": "06:00",
        "required_role": "Registered Nurse", "required_staff_count": "3",
        "shift_status": "Open", "notes": "Updated",
    })
    assert response.headers["HX-Trigger"] == "shifts-updated"
    assert updated["required_staff_count"] == 3 and updated["shift_status"] == "Open"
    assert "Shift updated." in response.data.decode()


def test_edit_custom_shift_times_infer_custom_template(
        frontend_client, fe_api_client, monkeypatch):
    _stub_shift_form_options(monkeypatch, fe_api_client)
    custom_shift = {**SHIFT_FIXTURE, "start_time": "08:00", "end_time": "16:00"}
    monkeypatch.setattr(fe_api_client, "get_shift", lambda sid: {"shift": custom_shift})

    form = frontend_client.get("/partials/shifts/11/edit").data.decode()

    assert 'value="Custom" selected' in form
    assert 'name="start_time" type="time"\n             value="08:00"' in form
    assert 'name="end_time" type="time"\n             value="16:00"' in form


def test_editing_only_required_count_preserves_all_select_values(
        frontend_client, fe_api_client, monkeypatch):
    updated = {}
    monkeypatch.setattr(
        fe_api_client, "update_shift",
        lambda sid, payload: updated.update(payload) or {"shift": SHIFT_FIXTURE})
    _stub_shift_detail(monkeypatch, fe_api_client)

    response = frontend_client.post("/partials/shifts/11", data={
        "department": "Emergency", "shift_date": "2026-08-27",
        "start_time": "23:00", "end_time": "07:00",
        "required_role": "Registered Nurse", "required_staff_count": "4",
        "shift_status": "Filled", "notes": "Night shift.",
    })

    assert response.status_code == 200
    assert updated["required_staff_count"] == 4
    assert updated["department"] == "Emergency"
    assert updated["required_role"] == "Registered Nurse"
    assert updated["shift_status"] == "Filled"


def test_invalid_submitted_role_is_preserved_in_validation_form(
        frontend_client, fe_api_client, monkeypatch):
    import api_client
    _stub_shift_form_options(monkeypatch, fe_api_client)
    monkeypatch.setattr(
        fe_api_client, "create_shift",
        lambda payload: (_ for _ in ()).throw(
            api_client.ValidationFailed("Required role is not recognised: 'Super Nurse'.")))

    body = frontend_client.post("/partials/shifts", data={
        "department": "Emergency", "shift_date": "2026-08-27",
        "start_time": "07:00", "end_time": "15:00",
        "required_role": "Super Nurse", "required_staff_count": "2",
        "shift_status": "Planned",
    }).data.decode()

    assert "Required role is not recognised" in body
    assert 'value="Super Nurse" selected' in body
    assert "Staff &amp; Shift service returned" not in body


def test_delete_shift_uses_hard_delete_and_refreshes(
        frontend_client, fe_api_client, monkeypatch):
    deleted = []
    monkeypatch.setattr(fe_api_client, "delete_shift",
                        lambda sid: deleted.append(sid) or {"message": "deleted"})
    response = frontend_client.post("/partials/shifts/11/delete")
    assert deleted == [11]
    assert response.headers["HX-Trigger"] == "shifts-updated"
    assert "permanently deleted" in response.data.decode()


def test_shift_detail_lists_active_assignments_and_candidates(
        frontend_client, fe_api_client, monkeypatch):
    assignments = [
        {"staff_id": 1, "name": "Amara Okafor", "role": "Registered Nurse",
         "department": "Intensive Care", "assignment_status": "Assigned"},
        {"staff_id": 12, "name": "Chloe Bennett", "role": "Registered Nurse",
         "department": "Intensive Care", "assignment_status": "Cancelled"},
    ]
    candidates = [
        {"staff_id": 1, "name": "Amara Okafor", "role": "Registered Nurse",
         "department": "Intensive Care", "availability_status": "Available",
         "specialisation": "Triage"},
        {"staff_id": 12, "name": "Chloe Bennett", "role": "Registered Nurse",
         "department": "Intensive Care", "availability_status": "Available",
         "specialisation": "Critical Care"},
    ]
    _stub_shift_detail(monkeypatch, fe_api_client, assignments, candidates)
    body = frontend_client.get("/partials/shifts/11").data.decode()
    assert "Amara Okafor" in body
    assert "Gap 1" in body
    assert "Chloe Bennett" in body
    # One active assignment plus both listed candidates are from another
    # department. Department remains context, never an eligibility block.
    assert body.count("Cross-department") == 3
    assert 'value="12"' in body and ">Assign</button>" in body
    assert "deterministic rules, not an AI" in body


def test_shift_detail_not_found_state(frontend_client, fe_api_client, monkeypatch):
    import api_client
    monkeypatch.setattr(
        fe_api_client, "get_shift",
        lambda sid: (_ for _ in ()).throw(api_client.NotFoundError("missing")))
    body = frontend_client.get("/partials/shifts/999").data.decode()
    assert "Shift not found" in body


def test_shift_detail_suppresses_candidates_when_assignments_unavailable(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_shift", lambda sid: {"shift": SHIFT_FIXTURE})
    monkeypatch.setattr(fe_api_client, "list_shift_assignments", _raise_unavailable)
    monkeypatch.setattr(
        fe_api_client, "list_staff",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not be called")))
    body = frontend_client.get("/partials/shifts/11").data.decode()
    assert "Assignment data is unavailable" in body
    assert "until current assignments can be loaded" in body


def test_assign_staff_success_and_duplicate_failure(
        frontend_client, fe_api_client, monkeypatch):
    calls = []
    monkeypatch.setattr(fe_api_client, "assign_staff",
                        lambda sid, staff_id: calls.append((sid, staff_id)) or {})
    _stub_shift_detail(monkeypatch, fe_api_client)
    response = frontend_client.post("/partials/shifts/11/assign", data={"staff_id": "12"})
    assert calls == [(11, 12)]
    assert response.headers["HX-Trigger"] == "shifts-updated"
    assert "Staff assigned to shift." in response.data.decode()

    import api_client
    monkeypatch.setattr(
        fe_api_client, "assign_staff",
        lambda sid, staff_id: (_ for _ in ()).throw(
            api_client.BackendError("Staff 12 is already assigned to shift 11.")))
    duplicate = frontend_client.post(
        "/partials/shifts/11/assign", data={"staff_id": "12"}).data.decode()
    assert "Staff was not assigned" in duplicate
    assert "already assigned" in duplicate


def test_assign_rejects_invalid_staff_id_without_api_call(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(
        fe_api_client, "assign_staff",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not be called")))
    _stub_shift_detail(monkeypatch, fe_api_client)
    body = frontend_client.post(
        "/partials/shifts/11/assign", data={"staff_id": "not-a-number"}).data.decode()
    assert "Select a valid staff member" in body


def test_unassign_staff_success_refreshes_coverage(
        frontend_client, fe_api_client, monkeypatch):
    calls = []
    monkeypatch.setattr(fe_api_client, "unassign_staff",
                        lambda sid, staff_id: calls.append((sid, staff_id)) or {})
    _stub_shift_detail(monkeypatch, fe_api_client)
    response = frontend_client.post(
        "/partials/shifts/11/unassign", data={"staff_id": "1"})
    assert calls == [(11, 1)]
    assert response.headers["HX-Trigger"] == "shifts-updated"
    assert "Staff unassigned from shift." in response.data.decode()


# ------------------------------------------ shift planner iteration 2
WEEK_SHIFTS = [
    {
        "shift_id": 20, "department": "Emergency", "shift_date": "2026-08-24",
        "start_time": "19:00", "end_time": "07:00",
        "required_role": "Registered Nurse", "required_staff_count": 3,
        "shift_status": "Planned", "notes": "Overnight coverage.",
    },
    {
        "shift_id": 21, "department": "Emergency", "shift_date": "2026-08-26",
        "start_time": "07:00", "end_time": "15:00",
        "required_role": "Registered Nurse", "required_staff_count": 3,
        "shift_status": "Open", "notes": None,
    },
    {
        "shift_id": 22, "department": "Emergency", "shift_date": "2026-08-26",
        "start_time": "07:00", "end_time": "15:00",
        "required_role": "Doctor", "required_staff_count": 1,
        "shift_status": "Open", "notes": None,
    },
    {
        "shift_id": 23, "department": "ICU", "shift_date": "2026-08-26",
        "start_time": "08:00", "end_time": "16:00",
        "required_role": "Registered Nurse", "required_staff_count": 2,
        "shift_status": "Filled", "notes": None,
    },
]

WEEK_COVERAGE = {
    "summary": {"total_shifts": 4, "total_shortfall": 4},
    "shifts": [
        {"shift_id": 20, "assigned_staff_count": 1},
        {"shift_id": 21, "assigned_staff_count": 2},
        {"shift_id": 22, "assigned_staff_count": 0},
        {"shift_id": 23, "assigned_staff_count": 2},
    ],
}


def _stub_week_planner(monkeypatch, fe_api_client):
    monkeypatch.setattr(
        fe_api_client, "list_shifts",
        lambda **kwargs: {"count": len(WEEK_SHIFTS), "shifts": WEEK_SHIFTS})
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **kwargs: WEEK_COVERAGE)


def test_week_planner_aggregates_daily_demand_and_real_department_gaps(
        frontend_client, fe_api_client, monkeypatch):
    _stub_week_planner(monkeypatch, fe_api_client)
    body = frontend_client.get(
        "/partials/planner?week_start=2026-08-24&selected_date=2026-08-26"
        "&department=Emergency").data.decode()

    assert "Week of 24 August – 30 August 2026" in body
    assert "Emergency — week grid" in body
    assert "<dt>Required today</dt><dd>4</dd>" in body
    assert "<dt>Assigned</dt><dd>2</dd>" in body
    assert "<dt>Coverage</dt><dd>50%</dd>" in body
    assert "<dt>Staffing gap</dt><dd>2</dd>" in body
    assert 'aria-label="2 unfilled"' in body
    assert "No shift" in body
    assert "<strong>Morning</strong>" in body
    assert "<strong>Afternoon</strong>" in body
    assert "<strong>Night</strong>" in body
    assert "<strong>Evening</strong>" not in body


def test_custom_shift_times_map_to_the_three_presentation_bands():
    fe = _fe()
    assert fe._shift_period("08:00") == "Morning"
    assert fe._shift_period("19:00") == "Afternoon"
    assert fe._shift_period("22:00") == "Night"


def test_week_grid_preserves_multiple_real_shifts_in_one_cell(
        frontend_client, fe_api_client, monkeypatch):
    _stub_week_planner(monkeypatch, fe_api_client)
    body = frontend_client.get(
        "/partials/planner?week_start=2026-08-24&selected_date=2026-08-26"
        "&department=Emergency").data.decode()

    assert '"selected_shift_id":"21"' in body
    assert '"selected_shift_id":"22"' in body
    assert "2/3" in body
    assert "0/1" in body
    assert "Registered Nurse" in body and "Doctor" in body


def test_planner_role_and_status_filters_are_server_derived(
        frontend_client, fe_api_client, monkeypatch):
    _stub_week_planner(monkeypatch, fe_api_client)
    body = frontend_client.get(
        "/partials/planner?week_start=2026-08-24&selected_date=2026-08-26"
        "&department=Emergency&required_role=Doctor&shift_status=Open").data.decode()

    assert "<dt>Required today</dt><dd>1</dd>" in body
    assert "<dt>Assigned</dt><dd>0</dd>" in body
    assert "Short 1" in body
    assert '<option value="Doctor" selected>' in body
    assert '<option value="Open" selected>' in body


def test_day_timeline_renders_real_positions_and_overnight_tail(
        frontend_client, fe_api_client, monkeypatch):
    _stub_week_planner(monkeypatch, fe_api_client)
    body = frontend_client.get(
        "/partials/planner?week_start=2026-08-24&selected_date=2026-08-25"
        "&department=Emergency&view=timeline").data.decode()

    assert "Tuesday 25 August — day timeline" in body
    assert 'width:29.1667%' in body
    assert "Emergency 19:00 to 07:00" in body
    assert 'class="view-toggle__button is-active"' in body
    assert 'aria-pressed="true"' in body
    assert '>Day timeline</button>' in body
    assert 'class="planner-layout" hx-target="#planner-workspace"' in body
    assert "All departments, 24-hour view" in body


def test_planner_model_handles_overnight_and_current_marker():
    frontend_module = _load_frontend_app_module()
    model = frontend_module._build_planner_model(
        WEEK_SHIFTS, WEEK_COVERAGE["shifts"], "2026-08-24", "2026-08-25",
        selected_department="Emergency", view="timeline",
        now=frontend_module.datetime.datetime(2026, 8, 25, 14, 32))

    emergency = next(row for row in model["timeline_rows"]
                     if row["department"] == "Emergency")
    segment = emergency["segments"][0]
    assert segment["continues_from_previous"] is True
    assert segment["segment_start"] == 0
    assert segment["segment_duration"] == 420
    assert model["now_marker"] == {"label": "14:32", "left_pct": 60.5556}


def test_week_summary_uses_real_shift_position_and_assignment_totals(
        frontend_client, fe_api_client, monkeypatch):
    _stub_week_planner(monkeypatch, fe_api_client)
    body = frontend_client.get(
        "/partials/planner?week_start=2026-08-24&selected_date=2026-08-26"
        "&department=Emergency").data.decode()

    assert "Week summary · Emergency" in body
    assert "<dt>Shifts</dt><dd>3</dd>" in body
    assert "<dt>Positions</dt><dd>7</dd>" in body
    assert "<dt>Filled</dt><dd>3</dd>" in body
    assert "<dt>Coverage</dt><dd>43%</dd>" in body


def test_planner_starts_with_an_empty_shift_selection(
        frontend_client, fe_api_client, monkeypatch):
    _stub_week_planner(monkeypatch, fe_api_client)
    body = frontend_client.get(
        "/partials/planner?week_start=2026-08-24&selected_date=2026-08-26"
        "&department=Emergency").data.decode()

    assert "Select a shift" in body
    assert "Choose a real shift in the grid or timeline" in body


def test_planner_real_shift_selection_reuses_management_panel(
        frontend_client, fe_api_client, monkeypatch):
    _stub_week_planner(monkeypatch, fe_api_client)
    selected = WEEK_SHIFTS[1]
    monkeypatch.setattr(fe_api_client, "get_shift", lambda sid: {"shift": selected})
    monkeypatch.setattr(
        fe_api_client, "list_shift_assignments",
        lambda sid: {"count": 1, "assignments": [{
            "staff_id": 1, "name": "Amara Okafor", "role": "Registered Nurse",
            "department": "Emergency", "assignment_status": "Assigned",
        }]})
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kwargs: {"count": 0, "staff": []})

    body = frontend_client.get(
        "/partials/planner?week_start=2026-08-24&selected_date=2026-08-26"
        "&department=Emergency&selected_shift_id=21").data.decode()
    assert "Shift detail" in body
    assert "Amara Okafor" in body
    assert 'name="panel" value="1"' in body
    assert 'hx-target="#planner-detail"' in body


def test_planner_backend_and_database_failures_are_isolated(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts", _raise_unavailable)
    unavailable = frontend_client.get("/partials/planner").data.decode()
    assert "Shift planner unavailable" in unavailable
    assert UNAVAILABLE_MESSAGE_HTML in unavailable

    import api_client
    monkeypatch.setattr(
        fe_api_client, "list_shifts",
        lambda **kwargs: (_ for _ in ()).throw(
            api_client.BackendError(
                "Staff & Shift service returned 503: Database service unreachable")))
    database = frontend_client.get("/partials/planner").data.decode()
    assert "Database service unreachable" in database


def test_planner_returned_fragment_cannot_self_trigger():
    """The planner fragment must not be able to re-request itself.

    A "load" trigger is permitted only on an element that also sets
    hx-target="this", i.e. one that swaps its own contents. Such an element
    cannot re-arm itself, because the partial it receives carries no
    hx-trigger of its own (asserted separately below). Anything else with a
    load trigger would fire again on every workspace swap and loop.
    """
    import re
    from pathlib import Path
    fragment = (Path(__file__).resolve().parents[1]
                / "frontend/templates/partials/planner_workspace.html").read_text()

    for match in re.finditer(r'<[^>]*hx-trigger="[^"]*\bload\b[^"]*"[^>]*>', fragment):
        assert 'hx-target="this"' in match.group(0), (
            "load trigger without hx-target=\"this\": " + match.group(0)[:120])


def test_roster_status_response_carries_no_trigger():
    """The roster-status partial must never re-arm the load that fetched it."""
    from pathlib import Path
    import re
    partial = (Path(__file__).resolve().parents[1]
               / "frontend/templates/partials/roster_status.html").read_text()
    # Strip Jinja comments: only real markup matters here.
    markup = re.sub(r"\{#.*?#\}", "", partial, flags=re.S)
    assert "hx-trigger" not in markup


# ------------------------------------------- weekly availability (UI)
def _weekly(*specs):
    return {"periods": [{"day_of_week": d, "start_time": s, "end_time": e}
                        for d, s, e in specs]}


def _drawer_stubs(monkeypatch, client_module, periods=None, shifts=None):
    monkeypatch.setattr(client_module, "get_staff",
                        lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(client_module, "list_staff_shifts",
                        lambda sid: {"shifts": shifts or []})
    monkeypatch.setattr(client_module, "get_weekly_availability",
                        lambda sid: periods if periods is not None else _weekly())


def _grid_cells(body):
    """Return only the grid's cell markup.

    The legend also uses the weekly-cell--* classes, so assertions must look
    at the table body rather than the whole drawer.
    """
    start = body.find("<tbody>", body.find("weekly-grid"))
    return body[start:body.find("</tbody>", start)] if start != -1 else ""


def test_weekly_grid_renders_three_bands(frontend_client, fe_api_client, monkeypatch):
    _drawer_stubs(monkeypatch, fe_api_client, _weekly((0, "07:00", "15:00")))
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "Weekly availability" in body
    for band in ("Morning", "Afternoon", "Night"):
        assert band in body


def test_weekly_grid_marks_available_cell(frontend_client, fe_api_client, monkeypatch):
    _drawer_stubs(monkeypatch, fe_api_client, _weekly((0, "07:00", "15:00")))
    cells = _grid_cells(frontend_client.get("/partials/staff/1").data.decode())
    assert "weekly-cell--available" in cells
    # Sparse model: everything not stored renders as not available.
    assert "weekly-cell--unavailable" in cells


def test_weekly_grid_empty_pattern_is_all_unavailable(frontend_client, fe_api_client, monkeypatch):
    _drawer_stubs(monkeypatch, fe_api_client, _weekly())
    cells = _grid_cells(frontend_client.get("/partials/staff/1").data.decode())
    assert "weekly-cell--available" not in cells
    assert "weekly-cell--rostered" not in cells


def test_weekly_grid_overlays_real_assignment_as_rostered(frontend_client, fe_api_client, monkeypatch):
    """A real assignment in the displayed week upgrades an available cell to
    rostered, without changing the stored pattern."""
    import datetime
    monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
    shift = {"shift_id": 1, "department": "Emergency", "shift_date": monday.isoformat(),
             "start_time": "07:00", "end_time": "15:00", "shift_status": "Planned",
             "assignment_id": 1, "assignment_status": "Confirmed"}
    _drawer_stubs(monkeypatch, fe_api_client, _weekly((0, "07:00", "15:00")), [shift])
    cells = _grid_cells(frontend_client.get("/partials/staff/1").data.decode())
    assert "weekly-cell--rostered" in cells


def test_rostered_requires_stored_availability(frontend_client, fe_api_client, monkeypatch):
    """An assignment on a day with no stored availability must not invent one."""
    import datetime
    monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
    shift = {"shift_id": 1, "department": "Emergency", "shift_date": monday.isoformat(),
             "start_time": "07:00", "end_time": "15:00", "shift_status": "Planned",
             "assignment_id": 1, "assignment_status": "Confirmed"}
    _drawer_stubs(monkeypatch, fe_api_client, _weekly(), [shift])
    cells = _grid_cells(frontend_client.get("/partials/staff/1").data.decode())
    assert "weekly-cell--rostered" not in cells


def test_weekly_section_survives_backend_failure(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": STAFF_FIXTURE[0]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "get_weekly_availability", _raise_unavailable)
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "Amara Okafor" in body                      # drawer still renders
    assert "Weekly availability is unavailable" in body


def test_weekly_editor_loads_with_stored_slots_checked(frontend_client, fe_api_client, monkeypatch):
    _drawer_stubs(monkeypatch, fe_api_client, _weekly((0, "07:00", "15:00")))
    body = frontend_client.get(
        "/partials/staff/1/weekly-availability/edit").data.decode()
    assert 'value="0-0"' in body
    # 21 slots: 7 days x 3 bands, each with a distinct day-band value.
    import re
    assert len(set(re.findall(r'value="([0-6]-[0-2])"', body))) == 21


def test_weekly_editor_save_persists_structured_periods(frontend_client, fe_api_client, monkeypatch):
    captured = {}

    def fake_replace(staff_id, periods):
        captured["periods"] = periods
        return {"periods": periods}

    _drawer_stubs(monkeypatch, fe_api_client, _weekly())
    monkeypatch.setattr(fe_api_client, "replace_weekly_availability", fake_replace)
    response = frontend_client.post("/partials/staff/1/weekly-availability",
                                    data={"slot": ["0-0", "3-2"]})
    assert response.status_code == 200
    # UI symbols are never persisted — slots expand to real times.
    assert captured["periods"] == [
        {"day_of_week": 0, "start_time": "07:00", "end_time": "15:00"},
        {"day_of_week": 3, "start_time": "23:00", "end_time": "07:00"},
    ]
    assert "Weekly availability updated." in response.data.decode()


def test_weekly_editor_save_ignores_malformed_slots(frontend_client, fe_api_client, monkeypatch):
    captured = {}
    _drawer_stubs(monkeypatch, fe_api_client, _weekly())
    monkeypatch.setattr(fe_api_client, "replace_weekly_availability",
                        lambda sid, periods: captured.setdefault("p", periods) or {"periods": periods})
    frontend_client.post("/partials/staff/1/weekly-availability",
                         data={"slot": ["0-0", "9-9", "bad", "2-99"]})
    assert captured["p"] == [{"day_of_week": 0, "start_time": "07:00", "end_time": "15:00"}]


def test_weekly_editor_save_failure_reports_error(frontend_client, fe_api_client, monkeypatch):
    _drawer_stubs(monkeypatch, fe_api_client, _weekly())
    monkeypatch.setattr(fe_api_client, "replace_weekly_availability", _raise_unavailable)
    body = frontend_client.post("/partials/staff/1/weekly-availability",
                                data={"slot": ["0-0"]}).data.decode()
    assert "Weekly availability was not saved." in body


def test_on_leave_retains_weekly_pattern(frontend_client, fe_api_client, monkeypatch):
    """A global status change must not erase the recurring schedule."""
    on_leave = {**STAFF_FIXTURE[0], "availability_status": "On Leave"}
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": on_leave})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "get_weekly_availability",
                        lambda sid: _weekly((0, "07:00", "15:00")))
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "weekly-cell--available" in body
    assert "overrides scheduling eligibility" in body


# --------------------------------- grid layout & row interaction (iter 5)
def test_weekly_grid_state_class_is_not_on_the_td(frontend_client, fe_api_client, monkeypatch):
    """A <td> with display:block leaves table layout and collapses all seven
    day columns into one — the state class must sit on an inner element."""
    _drawer_stubs(monkeypatch, fe_api_client, _weekly((0, "07:00", "15:00")))
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert 'td class="weekly-grid__cell weekly-cell--' not in body
    assert 'span class="weekly-cell weekly-cell--' in body


def test_weekly_grid_has_seven_day_columns_and_three_bands(frontend_client, fe_api_client, monkeypatch):
    import re
    _drawer_stubs(monkeypatch, fe_api_client, _weekly((0, "07:00", "15:00")))
    body = frontend_client.get("/partials/staff/1").data.decode()
    cells = _grid_cells(body)
    assert cells.count('<td class="weekly-grid__cell">') == 21     # 3 bands x 7 days
    assert len(re.findall(r'<tr>', cells)) == 3
    for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"):
        assert f'>{day}</th>' in body


def test_weekly_grid_declares_equal_day_columns(frontend_client, fe_api_client, monkeypatch):
    """A colgroup fixes the label column so the seven days share the rest."""
    _drawer_stubs(monkeypatch, fe_api_client, _weekly())
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert 'weekly-grid__labels' in body
    assert body.count("<col>") >= 7


def test_conflict_state_when_rostered_without_availability(frontend_client, fe_api_client, monkeypatch):
    """Derived only: a real assignment with no stored availability covering it."""
    import datetime
    monday = datetime.date.today() - datetime.timedelta(days=datetime.date.today().weekday())
    shift = {"shift_id": 1, "department": "Emergency", "shift_date": monday.isoformat(),
             "start_time": "07:00", "end_time": "15:00", "shift_status": "Planned",
             "assignment_id": 1, "assignment_status": "Confirmed"}
    _drawer_stubs(monkeypatch, fe_api_client, _weekly(), [shift])
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "weekly-cell--conflict" in _grid_cells(body)
    assert "Rostered outside availability" in body      # legend key appears


def test_no_conflict_legend_when_no_conflicts(frontend_client, fe_api_client, monkeypatch):
    _drawer_stubs(monkeypatch, fe_api_client, _weekly((0, "07:00", "15:00")))
    body = frontend_client.get("/partials/staff/1").data.decode()
    assert "Rostered outside availability" not in body


def test_whole_row_opens_detail_with_one_request(frontend_client, fe_api_client, monkeypatch):
    """The row owns the request so a click anywhere in it opens the drawer.

    Exactly one hx-get per row: if the inner buttons kept their own, a click
    would fire the button's request AND bubble to the row's, issuing two.
    """
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: {"count": 2, "staff": STAFF_FIXTURE})
    body = frontend_client.get("/partials/staff-table").data.decode()

    assert 'class="staff-row"' in body
    assert body.count('hx-get="/partials/staff/1"') == 1
    # Two rows in the fixture -> exactly two hx-get attributes in the table.
    assert body.count("hx-get=") == 2
    # Buttons remain focusable controls but carry no request of their own.
    assert '<button type="button" class="staff-identity staff-identity--action">' in body
    assert "button" in body and "hx-get" not in body.split("<button")[1].split(">")[0]


def test_staff_name_cell_is_keyboard_operable(frontend_client, fe_api_client, monkeypatch):
    """It must be a <button>, not a click-handler on a <td>."""
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: {"count": 1, "staff": STAFF_FIXTURE[:1]})
    body = frontend_client.get("/partials/staff-table").data.decode()
    assert '<td onclick' not in body and '<tr onclick' not in body
    assert 'open staff detail' in body                     # accessible name


def test_view_button_still_present(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: {"count": 1, "staff": STAFF_FIXTURE[:1]})
    body = frontend_client.get("/partials/staff-table").data.decode()
    assert "View" in body and "table__actions" in body


def test_weekly_editor_still_has_21_slots_and_colgroup(frontend_client, fe_api_client, monkeypatch):
    import re
    _drawer_stubs(monkeypatch, fe_api_client, _weekly((0, "07:00", "15:00")))
    body = frontend_client.get(
        "/partials/staff/1/weekly-availability/edit").data.decode()
    assert len(set(re.findall(r'value="([0-6]-[0-2])"', body))) == 21
    assert 'weekly-grid__labels' in body


# ==========================================================================
# Scenario A — weekly roster planning
# ==========================================================================
def _fe():
    return _load_frontend_app_module()


def _planner_shift(shift_id=1, date="2026-08-31", start="07:00", end="15:00",
           dept="Emergency", role="Registered Nurse", required=2, assigned=0,
           status="Assigned"):
    return {"shift_id": shift_id, "department": dept, "shift_date": date,
            "start_time": start, "end_time": end, "required_role": role,
            "required_staff_count": required, "assigned_staff_count": assigned,
            "shift_status": "Planned", "assignment_status": status}


def _person(staff_id=1, name="Amara Okafor", role="Registered Nurse",
            dept="Emergency", availability="Available", employment="Full-Time"):
    return {"staff_id": staff_id, "name": name, "role": role, "department": dept,
            "specialisation": None, "availability_status": availability,
            "employment_status": employment}


# ------------------------------------------------------------- overlap
def test_shifts_overlap_detects_partial_overlap():
    fe = _fe()
    a = _planner_shift(1, start="07:00", end="15:00")
    b = _planner_shift(2, start="14:00", end="22:00")
    assert fe._shifts_overlap(a, b) is True


def test_shifts_do_not_overlap_when_adjacent():
    fe = _fe()
    a = _planner_shift(1, start="07:00", end="15:00")
    b = _planner_shift(2, start="15:00", end="23:00")
    assert fe._shifts_overlap(a, b) is False


def test_overnight_shift_overlaps_into_next_day():
    """23:00-07:00 Monday runs into Tuesday and must clash with Tue 02:00."""
    fe = _fe()
    night = _planner_shift(1, date="2026-08-31", start="23:00", end="07:00")
    early = _planner_shift(2, date="2026-09-01", start="02:00", end="06:00")
    assert fe._shifts_overlap(night, early) is True


def test_shifts_on_different_days_do_not_overlap():
    fe = _fe()
    a = _planner_shift(1, date="2026-08-31", start="07:00", end="15:00")
    b = _planner_shift(2, date="2026-09-01", start="07:00", end="15:00")
    assert fe._shifts_overlap(a, b) is False


# ------------------------------------------- weekly availability cover
def test_weekly_covers_shift_exact_match():
    fe = _fe()
    periods = [{"day_of_week": 0, "start_time": "07:00", "end_time": "15:00"}]
    assert fe._weekly_covers_shift(periods, _planner_shift(date="2026-08-31")) is True


def test_weekly_does_not_cover_partial_shift():
    """Availability must cover the WHOLE shift window, not merely touch it."""
    fe = _fe()
    periods = [{"day_of_week": 0, "start_time": "07:00", "end_time": "14:00"}]
    assert fe._weekly_covers_shift(periods, _planner_shift(date="2026-08-31")) is False


def test_weekly_covers_overnight_shift():
    fe = _fe()
    periods = [{"day_of_week": 0, "start_time": "23:00", "end_time": "07:00"}]
    night = _planner_shift(date="2026-08-31", start="23:00", end="07:00")
    assert fe._weekly_covers_shift(periods, night) is True


def test_empty_weekly_availability_covers_nothing():
    fe = _fe()
    assert fe._weekly_covers_shift([], _planner_shift()) is False


# ----------------------------------------------------- eligibility
def test_candidate_eligible_with_matching_availability():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(), _planner_shift(), set(), [],
        [{"day_of_week": 0, "start_time": "07:00", "end_time": "15:00"}])
    assert result["eligible"] is True
    assert result["weekly_ok"] is True


def test_candidate_outside_weekly_availability_is_advisory_not_blocking():
    """The backend permits this, so it must warn rather than block."""
    fe = _fe()
    result = fe._evaluate_candidate(_person(), _planner_shift(), set(), [], [])
    assert result["eligible"] is True          # still assignable
    assert result["weekly_ok"] is False
    assert any("Outside weekly availability" in n["text"] for n in result["notes"])


def test_candidate_on_leave_is_blocked():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(availability="On Leave"), _planner_shift(), set(), [], [])
    assert result["eligible"] is False
    assert result["blocked_reason"] == "On Leave"


def test_candidate_unavailable_is_blocked():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(availability="Unavailable"), _planner_shift(), set(), [], [])
    assert result["eligible"] is False
    assert result["blocked_reason"] == "Unavailable"


def test_candidate_role_mismatch_is_blocked():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(role="Doctor"), _planner_shift(), set(), [], [])
    assert result["eligible"] is False
    assert result["blocked_reason"] == "Role mismatch"


def test_matching_role_in_another_department_remains_assignable():
    """Department is a sorting preference, never a hard eligibility rule."""
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(dept="Intensive Care"),
        _planner_shift(dept="Emergency"), set(), [],
        [{"day_of_week": 0, "start_time": "07:00", "end_time": "15:00"}])
    assert result["eligible"] is True
    assert result["department"] == "Intensive Care"
    assert result["blocked_reason"] is None


def test_candidate_already_assigned_to_this_shift_is_blocked():
    fe = _fe()
    result = fe._evaluate_candidate(_person(), _planner_shift(), {1}, [], [])
    assert result["eligible"] is False
    assert "Already assigned" in result["blocked_reason"]


def test_candidate_with_overlapping_assignment_is_blocked():
    fe = _fe()
    target = _planner_shift(shift_id=5, start="07:00", end="15:00")
    clash = _planner_shift(shift_id=9, start="08:00", end="16:00")
    result = fe._evaluate_candidate(_person(), target, set(), [clash], [])
    assert result["eligible"] is False
    assert "Already rostered" in result["blocked_reason"]


def test_cancelled_assignment_does_not_block_candidate():
    fe = _fe()
    target = _planner_shift(shift_id=5)
    cancelled = _planner_shift(shift_id=9, start="08:00", end="16:00", status="Cancelled")
    result = fe._evaluate_candidate(_person(), target, set(), [cancelled], [])
    assert result["eligible"] is True


# ------------------------------------------------- weekly summary
def test_week_summary_counts_positions():
    fe = _fe()
    shifts = [_planner_shift(1, required=4, assigned=3), _planner_shift(2, required=2, assigned=2)]
    summary = fe._week_roster_summary(shifts, [])
    assert summary["shift_count"] == 2
    assert summary["required_positions"] == 6
    assert summary["assigned_positions"] == 5
    assert summary["unfilled_positions"] == 1
    assert summary["ready"] is False


def test_week_summary_ready_when_filled_and_no_conflicts():
    fe = _fe()
    summary = fe._week_roster_summary([_planner_shift(1, required=2, assigned=2)], [])
    assert summary["unfilled_positions"] == 0
    assert summary["ready"] is True
    assert summary["label"] == "Roster ready"


def test_week_summary_not_ready_when_conflicts_exist():
    fe = _fe()
    summary = fe._week_roster_summary([_planner_shift(1, required=2, assigned=2)],
                                       [{"kind": "overlap"}])
    assert summary["ready"] is False


def test_empty_week_is_not_reported_as_ready():
    """No shifts planned is not a completed roster."""
    fe = _fe()
    summary = fe._week_roster_summary([], [])
    assert summary["empty"] is True
    assert summary["ready"] is False


# ------------------------------------------------ conflict detection
def _conflict_args(person, shift, weekly=None, other_shifts=None):
    return ([shift],
            {shift["shift_id"]: [{"staff_id": person["staff_id"],
                                   "assignment_status": "Assigned"}]},
            {person["staff_id"]: person},
            {person["staff_id"]: weekly or []},
            {person["staff_id"]: other_shifts or []})


def test_no_conflicts_when_everything_matches():
    fe = _fe()
    shift = _planner_shift(shift_id=1, date="2026-08-31")
    weekly = [{"day_of_week": 0, "start_time": "07:00", "end_time": "15:00"}]
    assert fe._week_conflicts(*_conflict_args(_person(), shift, weekly)) == []


def test_conflict_when_assigned_outside_weekly_availability():
    fe = _fe()
    conflicts = fe._week_conflicts(*_conflict_args(_person(), _planner_shift(shift_id=1)))
    assert any(c["kind"] == "availability" for c in conflicts)


def test_conflict_when_assigned_while_on_leave():
    fe = _fe()
    weekly = [{"day_of_week": 0, "start_time": "07:00", "end_time": "15:00"}]
    conflicts = fe._week_conflicts(
        *_conflict_args(_person(availability="On Leave"), _planner_shift(shift_id=1), weekly))
    assert any(c["kind"] == "status" and "On Leave" in c["detail"] for c in conflicts)


def test_conflict_when_assignments_overlap():
    fe = _fe()
    shift = _planner_shift(shift_id=1, start="07:00", end="15:00")
    other = _planner_shift(shift_id=9, start="08:00", end="16:00")
    weekly = [{"day_of_week": 0, "start_time": "00:00", "end_time": "23:59"}]
    conflicts = fe._week_conflicts(
        *_conflict_args(_person(), shift, weekly, [other]))
    assert any(c["kind"] == "overlap" for c in conflicts)


# --------------------------------------------------- roster status route
def test_roster_status_renders_real_totals(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage",
                        lambda **kw: {"shifts": [_planner_shift(1, date="2026-08-31",
                                                         required=2, assigned=1)]})
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **kw: {"staff": [_person()]})
    monkeypatch.setattr(fe_api_client, "list_shift_assignments",
                        lambda sid: {"assignments": []})
    body = frontend_client.get(
        "/partials/roster-status?week_start=2026-08-31").data.decode()
    assert "Weekly roster status" in body
    assert "31 Aug – 6 Sep 2026" in body       # real calculated dates
    assert "Roster incomplete" in body


def test_roster_status_backend_unavailable(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage", _raise_unavailable)
    body = frontend_client.get("/partials/roster-status").data.decode()
    assert "Roster status unavailable" in body


# ==========================================================================
# Scenario B — daily operational adjustment
# ==========================================================================
def _cov(shift_id=1, dept="Emergency", date="2026-08-27", start="07:00",
         end="15:00", role="Registered Nurse", required=2, assigned=1):
    return {"shift_id": shift_id, "department": dept, "shift_date": date,
            "start_time": start, "end_time": end, "required_role": role,
            "required_staff_count": required, "assigned_staff_count": assigned,
            "shortfall": max(required - assigned, 0),
            "coverage_status": ("Fully staffed" if assigned >= required
                                 else "Unstaffed" if assigned == 0 else "Understaffed")}


def _daily_stubs(monkeypatch, client_module, shifts):
    monkeypatch.setattr(client_module, "get_coverage",
                        lambda **kw: {"shifts": shifts,
                                      "summary": {"total_shifts": len(shifts)}})


# ------------------------------------------------- daily calculations
def test_daily_demand_aggregates_by_department(frontend_client, fe_api_client, monkeypatch):
    """Positions and shift counts are aggregated separately, not conflated."""
    _daily_stubs(monkeypatch, fe_api_client, [
        _cov(1, "Emergency", required=4, assigned=3),
        _cov(2, "Emergency", start="15:00", end="23:00", required=3, assigned=3),
        _cov(3, "Radiology", required=1, assigned=0),
    ])
    body = frontend_client.get("/partials/demand?date=2026-08-27").data.decode()
    assert "Emergency" in body and "Radiology" in body
    assert "6 / 7" in body            # 7 required positions across 2 shifts
    assert "2 shifts" in body         # shift count reported separately
    assert "Gap 1" in body


def test_daily_demand_orders_departments_with_gaps_first(frontend_client, fe_api_client, monkeypatch):
    _daily_stubs(monkeypatch, fe_api_client, [
        _cov(1, "Anaesthetics", required=1, assigned=1),   # alphabetically first
        _cov(2, "Radiology", required=1, assigned=0),      # but has the gap
    ])
    body = frontend_client.get("/partials/demand").data.decode()
    assert body.index("Radiology") < body.index("Anaesthetics")


def test_fully_staffed_day_still_lists_departments(frontend_client, fe_api_client, monkeypatch):
    _daily_stubs(monkeypatch, fe_api_client, [_cov(1, required=2, assigned=2)])
    body = frontend_client.get("/partials/demand").data.decode()
    assert "No staffing gaps today" in body
    assert "Fully staffed" in body
    assert "Emergency" in body        # information is not hidden


def test_no_shifts_today_is_reported_truthfully(frontend_client, fe_api_client, monkeypatch):
    _daily_stubs(monkeypatch, fe_api_client, [])
    body = frontend_client.get("/partials/demand?date=2030-01-01").data.decode()
    assert "No shifts scheduled for 2030-01-01" in body


def test_empty_day_coverage_is_dash_not_full(frontend_client, fe_api_client, monkeypatch):
    """Zero required positions must not render as 100% coverage."""
    monkeypatch.setattr(fe_api_client, "get_coverage",
                        lambda **kw: {"shifts": [], "summary": {
                            "total_shifts": 0, "fully_staffed": 0,
                            "understaffed": 0, "unstaffed": 0, "total_shortfall": 0}})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **kw: {"count": 0, "staff": []})
    body = frontend_client.get("/partials/kpis?date=2030-01-01").data.decode()
    assert "—" in body
    assert "100%" not in body


def test_daily_demand_backend_unavailable(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage", _raise_unavailable)
    body = frontend_client.get("/partials/demand").data.decode()
    assert "Operational demand unavailable" in body


# --------------------------------------------- dashboard -> planner link
def test_manage_link_carries_full_planner_context(frontend_client, fe_api_client, monkeypatch):
    """The manager must not have to find the same shift again."""
    _daily_stubs(monkeypatch, fe_api_client, [_cov(11, "Emergency", required=2, assigned=1)])
    body = frontend_client.get("/partials/demand?date=2026-08-27").data.decode()
    assert "date=2026-08-27" in body
    assert "department=Emergency" in body
    assert "selected_shift_id=11" in body
    assert "view=timeline" in body            # the planner's Day Timeline view


def test_manage_link_is_a_real_anchor_not_a_clickable_card(frontend_client, fe_api_client, monkeypatch):
    _daily_stubs(monkeypatch, fe_api_client, [_cov(11)])
    body = frontend_client.get("/partials/demand").data.decode()
    assert '<a class="btn-secondary btn-compact"' in body
    assert "<tr onclick" not in body and "<td onclick" not in body


def test_planner_seeds_state_from_deep_link(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts",
                        lambda **kw: {"shifts": [_cov(11)]})
    body = frontend_client.get(
        "/shifts?date=2026-08-27&department=Emergency"
        "&selected_shift_id=11&view=timeline").data.decode()
    assert 'name="department" value="Emergency"' in body
    assert 'name="selected_shift_id" value="11"' in body
    assert 'name="view" value="timeline"' in body
    assert 'name="selected_date" value="2026-08-27"' in body


def test_planner_rejects_invalid_deep_link_values(frontend_client, fe_api_client, monkeypatch):
    """Bad query values fall back to safe defaults rather than propagating."""
    monkeypatch.setattr(fe_api_client, "list_shifts", lambda **kw: {"shifts": []})
    body = frontend_client.get(
        "/shifts?department=Emergency&selected_shift_id=notanid&view=bogus").data.decode()
    assert 'name="selected_shift_id" value=""' in body
    assert 'name="view" value="week"' in body


def test_planner_without_deep_link_uses_defaults(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_shifts", lambda **kw: {"shifts": []})
    body = frontend_client.get("/shifts").data.decode()
    assert 'name="department" value=""' in body
    assert 'name="selected_shift_id" value=""' in body
    assert 'name="view" value="week"' in body


# ==========================================================================
# Scenario D — shift requirement changes
# ==========================================================================
def test_coverage_helper_reports_gap():
    fe = _fe()
    assert fe._coverage(2, 3)["label"] == "Gap 1"


def test_coverage_helper_reports_fully_staffed():
    fe = _fe()
    assert fe._coverage(3, 3)["label"] == "Fully staffed"


def test_coverage_helper_reports_overstaffed():
    """Requirement decreased below current assignments."""
    fe = _fe()
    assert fe._coverage(3, 2)["label"] == "Overstaffed by 1"


def test_planner_grid_distinguishes_overstaffed_from_covered():
    fe = _fe()
    assert fe._planning_coverage(3, 3)["label"] == "Covered"
    assert fe._planning_coverage(4, 3)["label"] == "Over 1"
    assert fe._planning_coverage(4, 3)["gap"] == 0


# ------------------------------------------- aggregation safety
def test_surplus_on_one_shift_does_not_offset_shortage_on_another():
    """The important case: extra staff on shift A cannot fill shift B."""
    fe = _fe()
    shifts = [_planner_shift(1, required=2, assigned=3),   # surplus 1
              _planner_shift(2, required=2, assigned=1)]   # gap 1
    summary = fe._week_roster_summary(shifts, [])
    assert summary["unfilled_positions"] == 1        # not 0
    assert summary["overstaffed_positions"] == 1
    # A naive net would be zero; per-shift accounting must not do that.
    assert summary["unfilled_positions"] != 0


def test_weekly_summary_not_ready_when_gap_hidden_behind_surplus():
    fe = _fe()
    shifts = [_planner_shift(1, required=1, assigned=5),
              _planner_shift(2, required=2, assigned=1)]
    summary = fe._week_roster_summary(shifts, [])
    assert summary["unfilled_positions"] == 1
    assert summary["ready"] is False


# ----------------------------------------------- coverage cap
def test_daily_coverage_cannot_exceed_100_percent(frontend_client, fe_api_client, monkeypatch):
    """An overstaffed shift must not inflate coverage above 100%."""
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **kw: {
        "shifts": [_cov(1, required=2, assigned=3)],
        "summary": {"total_shifts": 1, "total_shortfall": 0}})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **kw: {"count": 5, "staff": []})
    body = frontend_client.get("/partials/kpis?date=2026-08-24").data.decode()
    assert "150%" not in body
    assert "100%" in body
    assert "1 surplus" in body        # surplus reported separately


def test_daily_coverage_uses_filled_positions(frontend_client, fe_api_client, monkeypatch):
    """Surplus on one shift must not mask a shortage on another in the %."""
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **kw: {
        "shifts": [_cov(1, "Emergency", required=2, assigned=4),
                   _cov(2, "Radiology", required=2, assigned=0)],
        "summary": {"total_shifts": 2, "total_shortfall": 2}})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **kw: {"count": 5, "staff": []})
    body = frontend_client.get("/partials/kpis").data.decode()
    # filled = min(4,2) + min(0,2) = 2 of 4 required = 50%, not 100%.
    assert "50%" in body


# --------------------------------------- department roll-up with surplus
def test_department_rollup_reports_gap_and_surplus_independently(frontend_client, fe_api_client, monkeypatch):
    _daily_stubs(monkeypatch, fe_api_client, [
        _cov(1, "Emergency", required=2, assigned=3),                       # surplus
        _cov(2, "Emergency", start="15:00", end="23:00", required=2, assigned=1),  # gap
    ])
    body = frontend_client.get("/partials/demand").data.decode()
    rollup = body[body.index("daily-department-list"):body.index("table-wrap")]
    assert "Gap 1" in rollup
    assert "Overstaffed by 1" in rollup
    # The department has both a shortage and a surplus, so it is neither.
    assert "Fully staffed" not in rollup


def test_department_fully_staffed_when_no_gap_and_no_surplus(frontend_client, fe_api_client, monkeypatch):
    _daily_stubs(monkeypatch, fe_api_client, [_cov(1, required=2, assigned=2)])
    body = frontend_client.get("/partials/demand").data.decode()
    assert "Fully staffed" in body
    assert "Overstaffed" not in body


# ------------------------------------------------ requirement validation
def test_requirement_zero_is_rejected(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "update_shift",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    body = frontend_client.post("/partials/shifts/3", data={
        "department": "Emergency", "shift_date": "2026-08-24",
        "start_time": "07:00", "end_time": "15:00",
        "required_role": "Registered Nurse", "required_staff_count": "0",
        "shift_status": "Filled"}).data.decode()
    assert "greater than zero" in body


def test_requirement_non_numeric_is_rejected(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "update_shift",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    body = frontend_client.post("/partials/shifts/3", data={
        "department": "Emergency", "shift_date": "2026-08-24",
        "start_time": "07:00", "end_time": "15:00",
        "required_role": "Registered Nurse", "required_staff_count": "abc",
        "shift_status": "Filled"}).data.decode()
    assert "whole number" in body


def test_filled_lifecycle_badge_is_neutral_and_gap_remains_authoritative(
        frontend_client, fe_api_client, monkeypatch):
    filled = {**SHIFT_FIXTURE, "shift_status": "Filled"}
    monkeypatch.setattr(fe_api_client, "get_shift", lambda sid: {"shift": filled})
    monkeypatch.setattr(
        fe_api_client, "list_shift_assignments",
        lambda sid: {"assignments": [_assignment_row()]})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **kwargs: {"staff": []})

    body = frontend_client.get("/partials/shifts/11").data.decode()
    assert "Lifecycle" in body
    assert '<span class="badge-neutral">Filled</span>' in body
    assert '<span class="badge-success">Filled</span>' not in body
    assert "Gap 1" in body


def test_filled_shift_list_keeps_lifecycle_separate_from_coverage(
        frontend_client, fe_api_client, monkeypatch):
    filled = {**SHIFT_FIXTURE, "shift_status": "Filled"}
    monkeypatch.setattr(
        fe_api_client, "list_shifts", lambda **kwargs: {"shifts": [filled]})
    monkeypatch.setattr(
        fe_api_client, "get_coverage", lambda **kwargs: COVERAGE_FIXTURE)

    body = frontend_client.get(
        "/partials/shifts?shift_date=2026-08-27").data.decode()
    assert "Lifecycle · Filled" in body
    assert "Gap 1" in body
    assert '<span class="badge-success">Lifecycle · Filled</span>' not in body


def test_requirement_change_does_not_touch_assignments(frontend_client, fe_api_client, monkeypatch):
    """Editing the requirement must never add or remove staff."""
    calls = []
    monkeypatch.setattr(fe_api_client, "update_shift",
                        lambda sid, payload: calls.append(("update", payload)) or {"shift": {}})
    monkeypatch.setattr(fe_api_client, "assign_staff",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("auto-assign")))
    monkeypatch.setattr(fe_api_client, "unassign_staff",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("auto-unassign")))
    monkeypatch.setattr(fe_api_client, "get_shift", lambda sid: {"shift": _cov(3)})
    monkeypatch.setattr(fe_api_client, "list_shift_assignments", lambda sid: {"assignments": []})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **kw: {"staff": []})
    frontend_client.post("/partials/shifts/3", data={
        "department": "Emergency", "shift_date": "2026-08-24",
        "start_time": "07:00", "end_time": "15:00",
        "required_role": "Registered Nurse", "required_staff_count": "3",
        "shift_status": "Filled"})
    assert calls and calls[0][1]["required_staff_count"] == 3


# ==========================================================================
# Coverage correctness — surplus must never inflate coverage or mask a gap
# ==========================================================================
def _agg(*pairs):
    """pairs of (required, assigned)."""
    fe = _fe()
    return fe._aggregate_shifts([
        {"required_staff_count": r, "assigned_staff_count": a} for r, a in pairs])


def test_case_a_normal_shortage():
    a = _agg((4, 3))
    assert (a["filled"], a["gap"], a["surplus"], a["coverage_pct"]) == (3, 1, 0, 75)


def test_case_b_exact_coverage():
    a = _agg((4, 4))
    assert (a["filled"], a["gap"], a["surplus"], a["coverage_pct"]) == (4, 0, 0, 100)


def test_case_c_overstaffed_is_capped_at_100():
    a = _agg((2, 3))
    assert (a["filled"], a["gap"], a["surplus"]) == (2, 0, 1)
    assert a["coverage_pct"] == 100
    assert a["coverage_pct"] <= 100


def test_case_d_surplus_cannot_cancel_another_shifts_gap():
    """The critical case: A(req 2, asg 3) + B(req 2, asg 1)."""
    a = _agg((2, 3), (2, 1))
    assert a["required"] == 4
    assert a["filled"] == 3
    assert a["gap"] == 1          # NOT 0
    assert a["surplus"] == 1
    assert a["coverage_pct"] == 75    # NOT 100


def test_case_e_no_demand_is_dash_not_full():
    a = _agg()
    assert a["coverage_pct"] is None
    assert a["state"]["label"] == "No demand"


def test_aggregate_coverage_never_exceeds_100_across_many_shapes():
    for pairs in [((1, 9),), ((2, 3), (2, 3)), ((1, 5), (4, 0)), ((3, 4), (1, 1))]:
        a = _agg(*pairs)
        assert a["coverage_pct"] is None or a["coverage_pct"] <= 100, pairs


def test_aggregate_status_label_reflects_true_gap_not_netted_total():
    """Netting would label A+B "Covered"; the real state is a shortage."""
    assert _agg((2, 3), (2, 1))["state"]["label"] == "Short 1"


def test_aggregate_reports_over_when_only_surplus_exists():
    assert _agg((2, 3))["state"]["label"] == "Over 1"


def test_backend_total_shortfall_is_per_shift(client):
    """The backend must also floor shortfall per shift before summing."""
    body = client.get("/api/shifts/coverage").get_json()
    expected = sum(max(r["required_staff_count"] - r["assigned_staff_count"], 0)
                   for r in body["shifts"])
    assert body["summary"]["total_shortfall"] == expected


# ==========================================================================
# Scenario C — unavailability requests in the frontend
# ==========================================================================

def _req(request_id=1, staff_id=1, start="2026-08-31", end="2026-09-02",
         status="Approved", reason="Vacation", notes=None, name="Amara Okafor"):
    return {"request_id": request_id, "staff_id": staff_id,
            "start_date": start, "end_date": end, "reason": reason,
            "notes": notes, "request_status": status,
            "reviewed_by": "Nadia Whitfield" if status in ("Approved", "Rejected") else None,
            "reviewed_at": "2026-08-25 10:00" if status in ("Approved", "Rejected") else None,
            "created_at": "2026-08-20 09:00", "updated_at": "2026-08-25 10:00",
            "staff_name": name, "staff_role": "Registered Nurse",
            "staff_department": "Emergency"}


# ------------------------------------------------- eligibility blocking
def test_approved_request_covering_the_shift_blocks_the_candidate():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(), _planner_shift(date="2026-09-01"), set(), [], [],
        [_req(start="2026-08-31", end="2026-09-02")])
    assert result["eligible"] is False
    assert result["blocked_reason"] == "Temporarily unavailable 31 Aug – 2 Sep"


def test_blocking_is_inclusive_of_the_first_day():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(), _planner_shift(date="2026-08-31"), set(), [], [],
        [_req(start="2026-08-31", end="2026-09-02")])
    assert result["eligible"] is False


def test_blocking_is_inclusive_of_the_last_day():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(), _planner_shift(date="2026-09-02"), set(), [], [],
        [_req(start="2026-08-31", end="2026-09-02")])
    assert result["eligible"] is False


def test_shift_outside_the_approved_period_is_not_blocked():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(), _planner_shift(date="2026-09-03"), set(), [], [],
        [_req(start="2026-08-31", end="2026-09-02")])
    assert result["eligible"] is True
    assert result["approved_request"] is None


@pytest.mark.parametrize("status", ["Pending", "Rejected", "Cancelled"])
def test_only_an_approved_request_blocks_assignment(status):
    """A request that has not been granted must never affect the roster."""
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(), _planner_shift(date="2026-09-01"), set(), [], [],
        [_req(start="2026-08-31", end="2026-09-02", status=status)])
    assert result["eligible"] is True


def test_single_day_request_label_does_not_repeat_the_date():
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(), _planner_shift(date="2026-09-01"), set(), [], [],
        [_req(start="2026-09-01", end="2026-09-01")])
    assert result["blocked_reason"] == "Temporarily unavailable 1 Sep"


def test_candidate_evaluation_without_requests_is_unchanged():
    """The parameter is optional: existing callers keep working."""
    fe = _fe()
    result = fe._evaluate_candidate(_person(), _planner_shift(), set(), [], [])
    assert result["eligible"] is True
    assert result["approved_request"] is None


def test_blocking_request_helper_ignores_other_peoples_requests():
    """Grouping is by staff_id, so a request list is never applied to the
    wrong person."""
    fe = _fe()
    grouped = fe._requests_by_staff([_req(staff_id=2)])
    assert grouped.get(1) is None
    assert len(grouped[2]) == 1


# --------------------------------------------------- the R0 demo gate
def test_application_is_unreachable_without_choosing_a_role(anonymous_client):
    assert anonymous_client.get("/").status_code == 302
    assert anonymous_client.get("/shifts").status_code == 302
    assert anonymous_client.get("/me").status_code == 302


def test_demo_entry_screen_is_reachable(anonymous_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **k: {"staff": [_person()], "count": 1})
    response = anonymous_client.get("/demo")
    assert response.status_code == 200


def test_demo_entry_states_plainly_that_it_is_not_authentication(
        anonymous_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **k: {"staff": [_person()], "count": 1})
    body = anonymous_client.get("/demo").get_data(as_text=True)
    assert "not a login" in body
    assert "no authentication" in body


def test_demo_entry_survives_the_backend_being_down(
        anonymous_client, fe_api_client, monkeypatch):
    """The entry screen must still offer the manager role when staff records
    cannot be listed — otherwise a backend outage locks everyone out."""
    monkeypatch.setattr(fe_api_client, "list_staff", _raise_unavailable)
    response = anonymous_client.get("/demo")
    assert response.status_code == 200
    assert "Staff Manager" in response.get_data(as_text=True)


def test_htmx_request_without_a_role_asks_the_browser_to_navigate(anonymous_client):
    """A fragment cannot render a redirect, so the whole page must move."""
    response = anonymous_client.get("/partials/kpis", headers={"HX-Request": "true"})
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/demo"


def test_choosing_the_manager_role_enters_the_application(anonymous_client):
    response = anonymous_client.post("/demo/enter", data={"role": "Staff Manager"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_employee_role_requires_a_staff_member(anonymous_client):
    response = anonymous_client.post("/demo/enter", data={"role": "Employee"})
    assert response.headers["Location"].endswith("role=Employee")


def test_unknown_role_is_refused(anonymous_client):
    response = anonymous_client.post("/demo/enter", data={"role": "Administrator"})
    assert response.headers["Location"].endswith("/demo")


def test_leaving_the_demo_clears_the_identity(frontend_client):
    frontend_client.post("/demo/exit")
    assert frontend_client.get("/").status_code == 302


# ------------------------------------------------- role-aware chrome
def test_manager_chrome_shows_the_requests_section(frontend_client, fe_api_client,
                                                   monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage", _raise_unavailable)
    monkeypatch.setattr(fe_api_client, "list_staff", _raise_unavailable)
    body = frontend_client.get("/").get_data(as_text=True)
    assert 'href="/requests"' in body


def test_employee_chrome_offers_only_their_own_view(employee_client, fe_api_client,
                                                    monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff",
                        lambda sid: {"staff": _person()})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "get_weekly_availability",
                        lambda sid: {"periods": []})
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": []})
    body = employee_client.get("/me").get_data(as_text=True)
    assert 'href="/requests"' not in body
    assert 'href="/staff"' not in body
    assert 'href="/me"' in body


def test_identity_is_read_only_context_not_a_role_switcher(
        frontend_client, fe_api_client, monkeypatch):
    """A dropdown in the chrome would read as a sign-in control. The role is
    chosen once, on the separate demo screen, and shown here as text."""
    monkeypatch.setattr(fe_api_client, "get_coverage", _raise_unavailable)
    monkeypatch.setattr(fe_api_client, "list_staff", _raise_unavailable)
    body = frontend_client.get("/").get_data(as_text=True)
    assert "role-select" not in body
    assert "demo only, not authentication" in body


def test_employee_is_steered_away_from_manager_pages(employee_client):
    for path in ("/", "/staff", "/shifts", "/requests"):
        response = employee_client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/me")


# ------------------------------------------------------ employee view
@pytest.fixture
def employee_view(employee_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_staff", lambda sid: {"staff": _person()})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "get_weekly_availability",
                        lambda sid: {"periods": []})
    return employee_client


def test_my_workforce_lists_only_the_employees_own_requests(employee_view,
                                                            fe_api_client, monkeypatch):
    seen = {}

    def fake_list(staff_id, request_status=None):
        seen["staff_id"] = staff_id
        return {"requests": [_req(status="Pending")]}

    monkeypatch.setattr(fe_api_client, "list_staff_requests", fake_list)
    body = employee_view.get("/me").get_data(as_text=True)
    assert seen["staff_id"] == 1          # from the session, never the URL
    assert "31 Aug – 2 Sep" in body


def test_pending_request_offers_withdrawal(employee_view, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": [_req(status="Pending")]})
    assert "Withdraw" in employee_view.get("/me").get_data(as_text=True)


@pytest.mark.parametrize("status", ["Approved", "Rejected", "Cancelled"])
def test_resolved_request_cannot_be_withdrawn(employee_view, fe_api_client,
                                              monkeypatch, status):
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": [_req(status=status)]})
    body = employee_view.get("/me").get_data(as_text=True)
    # Assert on the control, not the word: a cancelled request legitimately
    # reads "Withdrawn by you." in its outcome line.
    assert "/cancel" not in body


def test_approved_request_tells_the_employee_the_roster_is_unchanged(
        employee_view, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": [_req(status="Approved")]})
    body = employee_view.get("/me").get_data(as_text=True)
    assert "not cancelled automatically" in body


def test_submitting_a_request_uses_the_session_staff_id(employee_view,
                                                        fe_api_client, monkeypatch):
    captured = {}

    def fake_create(staff_id, payload):
        captured["staff_id"], captured["payload"] = staff_id, payload
        return {"request": _req(status="Pending")}

    monkeypatch.setattr(fe_api_client, "create_staff_request", fake_create)
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": []})
    employee_view.post("/partials/me/requests", data={
        "start_date": "2026-09-01", "end_date": "2026-09-02", "reason": "Vacation"})
    assert captured["staff_id"] == 1
    assert captured["payload"]["reason"] == "Vacation"


def test_reversed_dates_are_refused_before_reaching_the_backend(
        employee_view, fe_api_client, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("the backend must not be called with invalid dates")

    monkeypatch.setattr(fe_api_client, "create_staff_request", fail)
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": []})
    body = employee_view.post("/partials/me/requests", data={
        "start_date": "2026-09-09", "end_date": "2026-09-01",
        "reason": "Personal"}).get_data(as_text=True)
    assert "end date cannot fall before the start date" in body


def test_rejected_submission_keeps_what_was_typed(employee_view, fe_api_client,
                                                  monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": []})
    body = employee_view.post("/partials/me/requests", data={
        "start_date": "2026-09-09", "end_date": "2026-09-01",
        "reason": "Personal", "notes": "Wedding."}).get_data(as_text=True)
    assert 'value="2026-09-09"' in body
    assert "Wedding." in body


def test_overlap_conflict_is_shown_without_http_plumbing(employee_view,
                                                         fe_api_client, monkeypatch):
    def conflict(staff_id, payload):
        raise fe_api_client.ConflictError(
            "This period overlaps an existing Pending request "
            "(2026-09-01 to 2026-09-03).")

    monkeypatch.setattr(fe_api_client, "create_staff_request", conflict)
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": []})
    body = employee_view.post("/partials/me/requests", data={
        "start_date": "2026-09-02", "end_date": "2026-09-02",
        "reason": "Personal"}).get_data(as_text=True)
    assert "overlaps an existing Pending request" in body
    assert "returned 409" not in body


def test_employee_panels_declare_no_load_trigger(employee_view, fe_api_client,
                                                 monkeypatch):
    """The bug this guards against: a partial that re-arms `load` on itself
    fires immediately on insertion and never stops."""
    monkeypatch.setattr(fe_api_client, "list_staff_requests",
                        lambda sid, **k: {"requests": [_req()]})
    body = employee_view.get("/partials/me/requests").get_data(as_text=True)
    assert "hx-trigger" not in body


# ------------------------------------------------------- manager queue
def test_queue_lists_requests_with_employee_context(frontend_client, fe_api_client,
                                                    monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_unavailability_requests",
                        lambda **k: {"requests": [_req(status="Pending")]})
    body = frontend_client.get("/partials/requests").get_data(as_text=True)
    assert "Amara Okafor" in body
    assert "Registered Nurse" in body


def test_queue_puts_pending_requests_first(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_unavailability_requests",
                        lambda **k: {"requests": [
                            _req(1, status="Approved", start="2026-08-01",
                                 end="2026-08-02", name="Approved Person"),
                            _req(2, status="Pending", start="2026-09-01",
                                 end="2026-09-02", name="Pending Person")]})
    body = frontend_client.get("/partials/requests?status=All").get_data(as_text=True)
    assert body.index("Pending Person") < body.index("Approved Person")


def test_queue_filters_by_status(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_unavailability_requests",
                        lambda **k: {"requests": [
                            _req(1, status="Pending", name="Pending Person"),
                            _req(2, status="Approved", name="Approved Person")]})
    body = frontend_client.get("/partials/requests?status=Pending").get_data(as_text=True)
    assert "Pending Person" in body
    assert "Approved Person" not in body


def test_queue_declares_no_load_trigger(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_unavailability_requests",
                        lambda **k: {"requests": [_req()]})
    body = frontend_client.get("/partials/requests").get_data(as_text=True)
    assert "load" not in [t.strip() for t in
                          _hx_triggers(body)]


def _hx_triggers(body):
    import re
    return [t for value in re.findall(r'hx-trigger="([^"]*)"', body)
            for t in value.split(",")]


def test_queue_page_owns_the_load_trigger(frontend_client):
    """The wrapper triggers the load; the response it swaps in must not."""
    body = frontend_client.get("/requests").get_data(as_text=True)
    assert 'hx-trigger="load, requests-changed from:body"' in body
    assert 'hx-target="this"' in body


def test_queue_refusal_is_surfaced_not_swallowed(frontend_client, fe_api_client,
                                                 monkeypatch):
    def forbidden(**kwargs):
        raise fe_api_client.ForbiddenError("This operation requires the Staff Manager role.")

    monkeypatch.setattr(fe_api_client, "list_unavailability_requests", forbidden)
    body = frontend_client.get("/partials/requests").get_data(as_text=True)
    assert "requires the Staff Manager role" in body


# ------------------------------------------------------ request detail
def test_detail_shows_affected_shifts_as_derived(frontend_client, fe_api_client,
                                                 monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Pending"),
                                      "affected_assignments": [
                                          {"shift_id": 7, "shift_date": "2026-09-01",
                                           "start_time": "07:00", "end_time": "15:00",
                                           "department": "Emergency",
                                           "assignment_status": "Assigned"}]})
    body = frontend_client.get("/partials/requests/1").get_data(as_text=True)
    assert "2026-09-01" in body
    assert "Calculated now from the live roster" in body


def test_detail_links_affected_shifts_into_the_existing_planner(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Pending"),
                                      "affected_assignments": [
                                          {"shift_id": 7, "shift_date": "2026-09-01",
                                           "start_time": "07:00", "end_time": "15:00",
                                           "department": "Emergency",
                                           "assignment_status": "Assigned"}]})
    body = frontend_client.get("/partials/requests/1").get_data(as_text=True)
    # The planner reads `selected_shift_id`, not `shift_id` — a link using the
    # wrong name opens the right day with nothing selected.
    assert "date=2026-09-01" in body
    assert "selected_shift_id=7" in body
    assert "view=week" in body


def test_detail_excludes_cancelled_assignments_from_the_conflict_list(
        frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Pending"),
                                      "affected_assignments": [
                                          {"shift_id": 7, "shift_date": "2026-09-01",
                                           "start_time": "07:00", "end_time": "15:00",
                                           "department": "Emergency",
                                           "assignment_status": "Cancelled"}]})
    body = frontend_client.get("/partials/requests/1").get_data(as_text=True)
    assert "No active assignments fall in this period" in body


def test_pending_request_offers_a_decision(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Pending"),
                                      "affected_assignments": []})
    body = frontend_client.get("/partials/requests/1").get_data(as_text=True)
    assert 'name="decision" value="Approved"' in body
    assert 'name="decision" value="Rejected"' in body


@pytest.mark.parametrize("status", ["Approved", "Rejected", "Cancelled"])
def test_resolved_request_offers_no_decision(frontend_client, fe_api_client,
                                             monkeypatch, status):
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status=status),
                                      "affected_assignments": []})
    body = frontend_client.get("/partials/requests/1").get_data(as_text=True)
    assert 'name="decision"' not in body
    assert "can no longer be changed" in body


def test_missing_request_renders_not_found(frontend_client, fe_api_client, monkeypatch):
    def missing(rid):
        raise fe_api_client.NotFoundError("Request 99 not found.")

    monkeypatch.setattr(fe_api_client, "get_unavailability_request", missing)
    response = frontend_client.get("/partials/requests/99")
    assert response.status_code == 404
    assert "Request not found" in response.get_data(as_text=True)


# ------------------------------------------------------------- review
def test_approving_sends_the_decision_and_the_reviewer(frontend_client, fe_api_client,
                                                       monkeypatch):
    captured = {}

    def fake_review(request_id, decision, reviewed_by):
        captured.update(request_id=request_id, decision=decision,
                        reviewed_by=reviewed_by)
        return {"request": _req(status="Approved")}

    monkeypatch.setattr(fe_api_client, "review_unavailability_request", fake_review)
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Approved"),
                                      "affected_assignments": []})
    frontend_client.post("/partials/requests/3/review", data={"decision": "Approved"})
    assert captured["request_id"] == 3
    assert captured["decision"] == "Approved"
    assert captured["reviewed_by"]


def test_review_asks_the_queue_to_refresh(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "review_unavailability_request",
                        lambda *a, **k: {"request": _req(status="Approved")})
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Approved"),
                                      "affected_assignments": []})
    response = frontend_client.post("/partials/requests/1/review",
                                    data={"decision": "Approved"})
    assert response.headers["HX-Trigger"] == "requests-changed"


def test_review_result_says_the_roster_did_not_change(frontend_client, fe_api_client,
                                                      monkeypatch):
    monkeypatch.setattr(fe_api_client, "review_unavailability_request",
                        lambda *a, **k: {"request": _req(status="Approved")})
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Approved"),
                                      "affected_assignments": []})
    body = frontend_client.post("/partials/requests/1/review",
                                data={"decision": "Approved"}).get_data(as_text=True)
    assert "The roster is unchanged" in body


def test_review_never_calls_assignment_endpoints(frontend_client, fe_api_client,
                                                 monkeypatch):
    """Approval records a decision. If it ever starts moving people around,
    this fails."""
    def forbidden(*a, **k):
        raise AssertionError("reviewing a request must not touch assignments")

    monkeypatch.setattr(fe_api_client, "assign_staff", forbidden)
    monkeypatch.setattr(fe_api_client, "unassign_staff", forbidden)
    monkeypatch.setattr(fe_api_client, "update_availability", forbidden)
    monkeypatch.setattr(fe_api_client, "review_unavailability_request",
                        lambda *a, **k: {"request": _req(status="Approved")})
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Approved"),
                                      "affected_assignments": []})
    assert frontend_client.post("/partials/requests/1/review",
                                data={"decision": "Approved"}).status_code == 200


def test_missing_decision_is_refused(frontend_client, fe_api_client, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("no decision was chosen")

    monkeypatch.setattr(fe_api_client, "review_unavailability_request", fail)
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Pending"),
                                      "affected_assignments": []})
    body = frontend_client.post("/partials/requests/1/review", data={}).get_data(as_text=True)
    assert "Choose whether to approve or reject" in body


def test_terminal_state_refusal_is_shown_to_the_manager(frontend_client, fe_api_client,
                                                        monkeypatch):
    def conflict(*a, **k):
        raise fe_api_client.ConflictError(
            "This request is Approved and cannot become Rejected. "
            "Decisions in Release 0 are final.")

    monkeypatch.setattr(fe_api_client, "review_unavailability_request", conflict)
    monkeypatch.setattr(fe_api_client, "get_unavailability_request",
                        lambda rid: {"request": _req(status="Approved"),
                                      "affected_assignments": []})
    body = frontend_client.post("/partials/requests/1/review",
                                data={"decision": "Rejected"}).get_data(as_text=True)
    assert "cannot become Rejected" in body


# ------------------------------------------------------- overview link
def test_overview_surfaces_the_pending_request_count(frontend_client, fe_api_client,
                                                     monkeypatch):
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **k: {
        "shifts": [], "summary": {"total_shortfall": 0, "total_shifts": 0}})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **k: {"staff": [], "count": 0})
    monkeypatch.setattr(fe_api_client, "list_unavailability_requests",
                        lambda **k: {"requests": [_req(1), _req(2)]})
    body = frontend_client.get("/partials/kpis").get_data(as_text=True)
    assert "Requests to review" in body
    assert 'href="/requests?status=Pending"' in body


def test_overview_survives_the_request_queue_being_unavailable(frontend_client,
                                                               fe_api_client, monkeypatch):
    """A request-queue outage must not blank the coverage figures."""
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **k: {
        "shifts": [], "summary": {"total_shortfall": 0, "total_shifts": 3}})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **k: {"staff": [], "count": 7})
    monkeypatch.setattr(fe_api_client, "list_unavailability_requests",
                        _raise_unavailable)
    body = frontend_client.get("/partials/kpis").get_data(as_text=True)
    assert "Request queue unavailable" in body
    assert "Shifts today" in body


# ------------------------------------------------- identity propagation
def test_identity_headers_travel_with_every_backend_call():
    """The backend cannot enforce a role it is never told about."""
    import demo_identity
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = "test-key"
    with app.test_request_context():
        from flask import session
        session[demo_identity.SESSION_KEY] = {
            "role": "Employee", "staff_id": 4, "name": "Liam"}
        headers = demo_identity.identity_headers()
    assert headers["X-HOMS-Role"] == "Employee"
    assert headers["X-HOMS-Staff-Id"] == "4"


def test_no_identity_sends_no_headers():
    import demo_identity
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = "test-key"
    with app.test_request_context():
        assert demo_identity.identity_headers() == {}


def test_an_employee_identity_without_a_staff_member_is_not_valid():
    """Every employee route is scoped to a person; a roleless id is useless."""
    import demo_identity
    from flask import Flask

    app = Flask(__name__)
    app.secret_key = "test-key"
    with app.test_request_context():
        from flask import session
        session[demo_identity.SESSION_KEY] = {"role": "Employee", "staff_id": None}
        assert demo_identity.current_identity() is None


# ==========================================================================
# Scenario E — unexpected roster maintenance (frontend)
# ==========================================================================

def _assigned_shift(shift_id=1, date=None, start="07:00", end="15:00",
                    dept="Emergency", status="Assigned"):
    """A row as list_staff_shifts returns it (shift fields + assignment state)."""
    if date is None:
        date = datetime.date.today().isoformat()
    return {"shift_id": shift_id, "department": dept, "shift_date": date,
            "start_time": start, "end_time": end,
            "required_role": "Registered Nurse", "required_staff_count": 2,
            "shift_status": "Planned", "assignment_status": status}


# ------------------------------------------------- affected-shift derivation
@pytest.mark.parametrize("status", ["Unavailable", "On Leave"])
def test_unavailable_staff_with_an_assignment_has_a_conflict(status):
    fe = _fe()
    shift = _assigned_shift()
    conflicts = fe._availability_conflicts(
        _person(availability=status), None, [shift])
    assert conflicts == [shift]


def test_available_staff_with_assignments_have_no_conflict():
    """Being rostered is only a problem when you cannot work."""
    fe = _fe()
    conflicts = fe._availability_conflicts(
        _person(availability="Available"), None, [_assigned_shift()])
    assert conflicts == []


def test_unavailable_staff_with_no_assignments_has_no_conflict():
    fe = _fe()
    assert fe._availability_conflicts(
        _person(availability="Unavailable"), None, []) == []


def test_multiple_assignments_are_all_reported():
    fe = _fe()
    shifts = [_assigned_shift(1), _assigned_shift(2), _assigned_shift(3)]
    conflicts = fe._availability_conflicts(
        _person(availability="Unavailable"), None, shifts)
    assert len(conflicts) == 3


def test_current_assignment_is_included_alongside_upcoming():
    fe = _fe()
    current = _assigned_shift(9)
    conflicts = fe._availability_conflicts(
        _person(availability="Unavailable"), current, [_assigned_shift(10)])
    assert [row["shift_id"] for row in conflicts] == [9, 10]


def test_a_shift_appearing_twice_is_counted_once():
    """Guards the count against a change in how current/upcoming are split."""
    fe = _fe()
    shift = _assigned_shift(4)
    conflicts = fe._availability_conflicts(
        _person(availability="Unavailable"), shift, [shift])
    assert len(conflicts) == 1


def test_missing_person_yields_no_conflict():
    fe = _fe()
    assert fe._availability_conflicts(None, None, [_assigned_shift()]) == []


@pytest.mark.parametrize("status", ["Cancelled", "Declined"])
def test_inactive_assignments_are_not_roster_conflicts(status):
    """A cancelled assignment no longer occupies a place on the shift, so it
    is nothing for the manager to resolve. _split_shifts filters these."""
    fe = _fe()
    current, upcoming = fe._split_shifts([_assigned_shift(status=status)])
    assert current is None and upcoming == []
    assert fe._availability_conflicts(
        _person(availability="Unavailable"), current, upcoming) == []


# -------------------------------------------------------------- deep link
def test_planner_link_uses_the_parameters_the_planner_reads():
    fe = _fe()
    link = fe._planner_link(_assigned_shift(7, date="2026-09-01", dept="Emergency"))
    assert "selected_shift_id=7" in link
    assert "date=2026-09-01" in link
    assert "view=week" in link
    assert "department=Emergency" in link


def test_planner_link_encodes_departments_containing_spaces():
    fe = _fe()
    link = fe._planner_link(_assigned_shift(1, dept="Intensive Care"))
    assert "Intensive+Care" in link or "Intensive%20Care" in link


def test_planner_link_omits_missing_fields_rather_than_sending_blanks():
    fe = _fe()
    link = fe._planner_link({"shift_id": 3, "shift_date": "2026-09-01"})
    assert "department=" not in link
    assert "selected_shift_id=3" in link


# ----------------------------------------------------- staff detail drawer
@pytest.fixture
def staff_drawer(frontend_client, fe_api_client, monkeypatch):
    """Drawer wired to a controllable staff record and shift list."""
    state = {"person": _person(availability="Available"),
             "shifts": [_assigned_shift()]}
    monkeypatch.setattr(fe_api_client, "get_staff",
                        lambda sid: {"staff": state["person"]})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts",
                        lambda sid: {"shifts": state["shifts"]})
    monkeypatch.setattr(fe_api_client, "get_weekly_availability",
                        lambda sid: {"periods": []})
    return frontend_client, state


def test_drawer_warns_when_unavailable_staff_remain_rostered(staff_drawer):
    client, state = staff_drawer
    state["person"] = _person(availability="Unavailable")
    body = client.get("/partials/staff/1").get_data(as_text=True)
    assert "Operational availability conflict" in body
    assert "still rostered to" in body


def test_drawer_says_plainly_that_nothing_was_unassigned(staff_drawer):
    client, state = staff_drawer
    state["person"] = _person(availability="Unavailable")
    body = client.get("/partials/staff/1").get_data(as_text=True)
    assert "Nothing has been unassigned" in body


def test_drawer_shows_no_conflict_for_available_staff(staff_drawer):
    client, _ = staff_drawer
    body = client.get("/partials/staff/1").get_data(as_text=True)
    assert "Operational availability conflict" not in body


def test_conflict_is_labelled_in_words_not_only_colour(staff_drawer):
    client, state = staff_drawer
    state["person"] = _person(availability="On Leave")
    body = client.get("/partials/staff/1").get_data(as_text=True)
    assert "On Leave" in body
    assert "conflict-panel__title" in body


def test_every_conflicting_shift_offers_a_manage_link(staff_drawer):
    client, state = staff_drawer
    state["person"] = _person(availability="Unavailable")
    state["shifts"] = [_assigned_shift(1), _assigned_shift(2)]
    body = client.get("/partials/staff/1").get_data(as_text=True)
    assert "selected_shift_id=1" in body
    assert "selected_shift_id=2" in body


def test_availability_update_asks_the_directory_to_refresh(staff_drawer,
                                                           fe_api_client, monkeypatch):
    """Directory badge and drawer must not disagree after a change."""
    client, state = staff_drawer
    monkeypatch.setattr(fe_api_client, "update_availability",
                        lambda sid, status: {"staff": state["person"]})
    response = client.post("/partials/staff/1/availability",
                           data={"availability_status": "Unavailable"})
    assert response.headers["HX-Trigger"] == "staff-updated"


def test_failed_availability_update_does_not_claim_success(staff_drawer,
                                                           fe_api_client, monkeypatch):
    client, _ = staff_drawer
    monkeypatch.setattr(fe_api_client, "update_availability", _raise_unavailable)
    response = client.post("/partials/staff/1/availability",
                           data={"availability_status": "Unavailable"})
    assert "HX-Trigger" not in response.headers
    assert "was not updated" in response.get_data(as_text=True)


def test_drawer_survives_the_shift_lookup_failing(staff_drawer, fe_api_client,
                                                  monkeypatch):
    """A conflict panel is worth less than the rest of the record."""
    client, state = staff_drawer
    state["person"] = _person(availability="Unavailable")
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", _raise_unavailable)
    response = client.get("/partials/staff/1")
    assert response.status_code == 200
    assert "Amara Okafor" in response.get_data(as_text=True)


def test_drawer_declares_no_load_trigger(staff_drawer):
    """The conflict panel is rendered by its parent, never by itself."""
    client, state = staff_drawer
    state["person"] = _person(availability="Unavailable")
    body = client.get("/partials/staff/1").get_data(as_text=True)
    assert "load" not in [t.strip() for t in _hx_triggers(body)]


# ------------------------------------------------------ shift detail panel
@pytest.fixture
def shift_drawer(frontend_client, fe_api_client, monkeypatch):
    state = {"assignments": [], "candidates": []}
    monkeypatch.setattr(fe_api_client, "get_shift",
                        lambda sid: {"shift": _planner_shift(shift_id=1, required=2)})
    monkeypatch.setattr(fe_api_client, "list_shift_assignments",
                        lambda sid: {"assignments": state["assignments"]})
    monkeypatch.setattr(fe_api_client, "list_staff",
                        lambda **k: {"staff": state["candidates"], "count": 0})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "get_weekly_availability",
                        lambda sid: {"periods": []})
    monkeypatch.setattr(fe_api_client, "list_unavailability_requests",
                        lambda **k: {"requests": []})
    return frontend_client, state


def _assignment_row(staff_id=1, name="Amara Okafor", availability="Available",
                    status="Assigned"):
    return {"staff_id": staff_id, "name": name, "role": "Registered Nurse",
            "department": "Emergency", "assignment_id": staff_id,
            "assignment_status": status, "availability_status": availability}


@pytest.mark.parametrize("status", ["Unavailable", "On Leave"])
def test_shift_flags_an_assigned_person_who_cannot_work(shift_drawer, status):
    client, state = shift_drawer
    state["assignments"] = [_assignment_row(availability=status)]
    body = client.get("/partials/shifts/1").get_data(as_text=True)
    assert f"Assigned while {status}" in body
    assert "operationally unavailable" in body


def test_shift_does_not_flag_available_assigned_staff(shift_drawer):
    client, state = shift_drawer
    state["assignments"] = [_assignment_row()]
    body = client.get("/partials/shifts/1").get_data(as_text=True)
    assert "Assigned while" not in body


def test_unavailable_assigned_staff_are_still_listed(shift_drawer):
    """Hiding them would hide the very problem the manager must resolve."""
    client, state = shift_drawer
    state["assignments"] = [_assignment_row(availability="Unavailable")]
    body = client.get("/partials/shifts/1").get_data(as_text=True)
    assert "Amara Okafor" in body
    assert "Unassign" in body


def test_shift_still_reports_them_in_the_assigned_count(shift_drawer):
    """Coverage counts persisted assignments; Scenario E did not change that."""
    client, state = shift_drawer
    state["assignments"] = [_assignment_row(1, "Amara Okafor", "Unavailable"),
                            _assignment_row(2, "Priya Nandakumar", "Available")]
    body = client.get("/partials/shifts/1").get_data(as_text=True)
    assert "2 active" in body
    assert "still count towards the assigned total" in body


# ------------------------------------------------------------- eligibility
@pytest.mark.parametrize("status,reason", [
    ("Unavailable", "Unavailable"),
    ("On Leave", "On Leave"),
])
def test_operationally_unavailable_staff_are_not_assignable(status, reason):
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(availability=status), _planner_shift(), set(), [], [])
    assert result["eligible"] is False
    assert result["blocked_reason"] == reason


def test_available_staff_continue_through_the_other_rules():
    """Available is not a free pass — it just stops blocking at this step."""
    fe = _fe()
    clash = _planner_shift(shift_id=9, start="08:00", end="16:00")
    result = fe._evaluate_candidate(
        _person(availability="Available"), _planner_shift(shift_id=5), set(),
        [clash], [])
    assert result["eligible"] is False
    assert "Already rostered" in result["blocked_reason"]


def test_approved_temporary_unavailability_still_blocks_after_scenario_e():
    """Scenario C's rule must survive Scenario E untouched."""
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(availability="Available"), _planner_shift(date="2026-09-01"),
        set(), [], [], [_req(start="2026-08-31", end="2026-09-02")])
    assert result["eligible"] is False
    assert "Temporarily unavailable" in result["blocked_reason"]


def test_unavailable_status_takes_precedence_over_a_role_mismatch():
    """Ordering check: the reason shown should be the operational one."""
    fe = _fe()
    result = fe._evaluate_candidate(
        _person(role="Doctor", availability="Unavailable"),
        _planner_shift(), set(), [], [])
    assert result["blocked_reason"] == "Unavailable"


# ---------------------------------------------------------- authorization
def test_employee_cannot_reach_the_staff_drawer_of_another_person(employee_client,
                                                                  fe_api_client,
                                                                  monkeypatch):
    def forbidden(staff_id):
        raise fe_api_client.ForbiddenError(
            "This operation requires the Staff Manager role.")

    monkeypatch.setattr(fe_api_client, "get_staff", forbidden)
    body = employee_client.get("/partials/staff/2").get_data(as_text=True)
    assert "requires the Staff Manager role" in body


def test_employee_availability_change_is_refused_by_the_backend(employee_client,
                                                                fe_api_client,
                                                                monkeypatch):
    """The frontend does not decide this; it reports what the backend says."""
    def forbidden(staff_id, status):
        raise fe_api_client.ForbiddenError(
            "This operation requires the Staff Manager role.")

    monkeypatch.setattr(fe_api_client, "update_availability", forbidden)
    monkeypatch.setattr(fe_api_client, "get_staff",
                        lambda sid: {"staff": _person()})
    monkeypatch.setattr(fe_api_client, "list_staff_shifts", lambda sid: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "get_weekly_availability",
                        lambda sid: {"periods": []})
    response = employee_client.post("/partials/staff/1/availability",
                                    data={"availability_status": "Unavailable"})
    assert "HX-Trigger" not in response.headers
    assert "requires the Staff Manager role" in response.get_data(as_text=True)


# ==========================================================================
# Shift Planner — All departments weekly overview
# ==========================================================================

def _week_shift(shift_id, dept, date="2026-08-25", required=2, assigned=2,
                start="07:00", end="15:00", role="Registered Nurse",
                status="Planned"):
    return {"shift_id": shift_id, "department": dept, "shift_date": date,
            "start_time": start, "end_time": end, "required_role": role,
            "required_staff_count": required, "shift_status": status}


def _coverage_row(shift_id, required=2, assigned=2):
    return {"shift_id": shift_id, "required_staff_count": required,
            "assigned_staff_count": assigned}


def _planner_model(shifts, coverage, department=None, week="2026-08-24",
                   date="2026-08-25", **kwargs):
    fe = _fe()
    return fe._build_planner_model(shifts, coverage, week, date,
                                   selected_department=department, **kwargs)


def _by_department(model):
    return {row["department"]: row for row in model["department_summary"]}


# -------------------------------------------------------- aggregation
def test_department_summary_reports_each_department_separately():
    model = _planner_model(
        [_week_shift(1, "Emergency", required=2),
         _week_shift(2, "Radiology", required=1)],
        [_coverage_row(1, 2, 1), _coverage_row(2, 1, 0)],
        department="All")
    rows = _by_department(model)
    assert rows["Emergency"]["gap"] == 1
    assert rows["Radiology"]["gap"] == 1


def test_fully_staffed_department():
    model = _planner_model([_week_shift(1, "Pharmacy", required=1)],
                           [_coverage_row(1, 1, 1)], department="All")
    row = _by_department(model)["Pharmacy"]
    assert (row["gap"], row["surplus"], row["coverage_pct"]) == (0, 0, 100)
    assert row["status"]["label"] == "Fully staffed"


def test_understaffed_department_reports_the_shortfall():
    model = _planner_model([_week_shift(1, "Radiology", required=3)],
                           [_coverage_row(1, 3, 1)], department="All")
    row = _by_department(model)["Radiology"]
    assert row["gap"] == 2
    assert row["status"]["label"] == "Gap 2"


def test_overstaffed_department_reports_surplus_not_a_gap():
    model = _planner_model([_week_shift(1, "Emergency", required=2)],
                           [_coverage_row(1, 2, 3)], department="All")
    row = _by_department(model)["Emergency"]
    assert (row["gap"], row["surplus"]) == (0, 1)
    assert row["status"]["label"] == "Overstaffed by 1"


def test_department_with_no_shifts_this_week_says_so():
    """A quiet department must not silently disappear from the overview."""
    model = _planner_model(
        [_week_shift(1, "Emergency"), _week_shift(2, "Surgery", date="2026-09-14")],
        [_coverage_row(1), _coverage_row(2)], department="All")
    row = _by_department(model)["Surgery"]
    assert row["shift_count"] == 0
    assert row["coverage_pct"] is None
    assert row["status"]["label"] == "No shifts"


def test_coverage_never_exceeds_one_hundred_percent():
    model = _planner_model([_week_shift(1, "Emergency", required=2)],
                           [_coverage_row(1, 2, 5)], department="All")
    assert _by_department(model)["Emergency"]["coverage_pct"] == 100


def test_filled_counts_positions_not_bodies():
    model = _planner_model([_week_shift(1, "Emergency", required=2)],
                           [_coverage_row(1, 2, 5)], department="All")
    row = _by_department(model)["Emergency"]
    assert row["assigned"] == 5
    assert row["filled"] == 2


# ------------------------------------------------------- surplus safety
def test_surplus_in_one_department_never_cancels_a_gap_in_another():
    """The exact netting bug this aggregation exists to prevent."""
    model = _planner_model(
        [_week_shift(1, "Emergency", required=2),
         _week_shift(2, "Radiology", required=2)],
        [_coverage_row(1, 2, 3), _coverage_row(2, 2, 1)],
        department="All")
    rows = _by_department(model)
    assert rows["Emergency"]["surplus"] == 1
    assert rows["Emergency"]["gap"] == 0
    assert rows["Radiology"]["gap"] == 1
    assert sum(r["gap"] for r in model["department_summary"]) == 1


def test_surplus_within_one_department_never_cancels_its_own_gap():
    """Same rule between two shifts of a single department."""
    model = _planner_model(
        [_week_shift(1, "Emergency", required=2),
         _week_shift(2, "Emergency", required=2, start="15:00", end="23:00")],
        [_coverage_row(1, 2, 3), _coverage_row(2, 2, 1)],
        department="All")
    row = _by_department(model)["Emergency"]
    assert (row["gap"], row["surplus"]) == (1, 1)


def test_summary_totals_reconcile_with_the_whole_week():
    fe = _fe()
    shifts = [_week_shift(1, "Emergency", required=3),
              _week_shift(2, "Radiology", required=1),
              _week_shift(3, "Surgery", required=2, date="2026-08-27")]
    coverage = [_coverage_row(1, 3, 2), _coverage_row(2, 1, 0), _coverage_row(3, 2, 2)]
    model = _planner_model(shifts, coverage, department="All")
    summary = model["department_summary"]
    assert sum(r["shift_count"] for r in summary) == model["week_summary"]["shift_count"]
    assert sum(r["required"] for r in summary) == model["week_summary"]["required"]
    assert sum(r["assigned"] for r in summary) == model["week_summary"]["assigned"]
    assert sum(r["gap"] for r in summary) == model["week_summary"]["gap"]


def test_departments_with_gaps_are_listed_first():
    model = _planner_model(
        [_week_shift(1, "Alpha", required=1), _week_shift(2, "Zulu", required=3)],
        [_coverage_row(1, 1, 1), _coverage_row(2, 3, 1)], department="All")
    assert model["department_summary"][0]["department"] == "Zulu"


# ------------------------------------------------------------- selection
def test_all_departments_is_an_explicit_selection():
    model = _planner_model([_week_shift(1, "Emergency")], [_coverage_row(1)],
                           department="All")
    assert model["showing_all_departments"] is True
    assert model["selected_department"] == "All"


def test_a_named_department_is_not_treated_as_all():
    model = _planner_model([_week_shift(1, "Emergency")], [_coverage_row(1)],
                           department="Emergency")
    assert model["showing_all_departments"] is False


def test_blank_department_still_falls_back_to_a_real_one():
    """Blank keeps its existing deep-link meaning and must not become 'All'."""
    model = _planner_model([_week_shift(1, "Emergency")], [_coverage_row(1)],
                           department="")
    assert model["showing_all_departments"] is False
    assert model["selected_department"] == "Emergency"


def test_unknown_department_falls_back_rather_than_showing_all():
    model = _planner_model([_week_shift(1, "Emergency")], [_coverage_row(1)],
                           department="Cardiology")
    assert model["showing_all_departments"] is False
    assert model["selected_department"] == "Emergency"


@pytest.mark.parametrize("week,date", [
    ("2026-08-17", "2026-08-17"),
    ("2026-08-24", "2026-08-25"),
    ("2026-08-31", "2026-08-31"),
])
def test_all_selection_survives_week_navigation(week, date):
    model = _planner_model([_week_shift(1, "Emergency")], [_coverage_row(1)],
                           department="All", week=week, date=date)
    assert model["showing_all_departments"] is True


def test_department_selection_survives_week_navigation():
    """Navigating weeks must not silently reset to another department."""
    model = _planner_model(
        [_week_shift(1, "Emergency"), _week_shift(2, "Surgery")],
        [_coverage_row(1), _coverage_row(2)],
        department="Surgery", week="2026-08-31", date="2026-08-31")
    assert model["selected_department"] == "Surgery"


# --------------------------------------------------- scope of the panels
def test_all_departments_week_summary_covers_every_department():
    model = _planner_model(
        [_week_shift(1, "Emergency", required=2),
         _week_shift(2, "Surgery", required=2)],
        [_coverage_row(1, 2, 2), _coverage_row(2, 2, 2)], department="All")
    assert model["week_summary"]["shift_count"] == 2
    assert model["week_summary"]["required"] == 4


def test_single_department_week_summary_stays_scoped_to_it():
    model = _planner_model(
        [_week_shift(1, "Emergency", required=2),
         _week_shift(2, "Surgery", required=2)],
        [_coverage_row(1, 2, 2), _coverage_row(2, 2, 2)], department="Emergency")
    assert model["week_summary"]["shift_count"] == 1


def test_all_departments_daily_panel_covers_every_department():
    """Showing one department's numbers under an 'All' label would be a lie."""
    model = _planner_model(
        [_week_shift(1, "Emergency", date="2026-08-25"),
         _week_shift(2, "Surgery", date="2026-08-25")],
        [_coverage_row(1), _coverage_row(2)], department="All", date="2026-08-25")
    assert {row["department"] for row in model["daily_rows"]} == {"Emergency", "Surgery"}


# ---------------------------------------------------------------- filters
def test_summary_respects_the_role_filter():
    model = _planner_model(
        [_week_shift(1, "Emergency", role="Registered Nurse"),
         _week_shift(2, "Surgery", role="Doctor")],
        [_coverage_row(1), _coverage_row(2)],
        department="All", required_role="Doctor")
    rows = _by_department(model)
    assert "Surgery" in rows
    assert "Emergency" not in rows


def test_summary_respects_the_status_filter():
    model = _planner_model(
        [_week_shift(1, "Emergency", status="Planned"),
         _week_shift(2, "Surgery", status="Cancelled")],
        [_coverage_row(1), _coverage_row(2)],
        department="All", shift_status="Cancelled")
    assert set(_by_department(model)) == {"Surgery"}


# ------------------------------------------------------------ empty week
def test_empty_week_reports_no_shifts_rather_than_full_coverage():
    model = _planner_model([_week_shift(1, "Emergency", date="2026-09-14")],
                           [_coverage_row(1)], department="All")
    assert model["week_summary"]["shift_count"] == 0
    assert model["week_summary"]["coverage_pct"] is None


# ------------------------------------------------------------- rendering
@pytest.fixture
def planner(frontend_client, fe_api_client, monkeypatch):
    state = {"shifts": [_week_shift(1, "Emergency", required=2),
                        _week_shift(2, "Radiology", required=1)],
             "coverage": [_coverage_row(1, 2, 1), _coverage_row(2, 1, 0)]}
    monkeypatch.setattr(fe_api_client, "list_shifts",
                        lambda **k: {"shifts": state["shifts"]})
    monkeypatch.setattr(fe_api_client, "get_coverage",
                        lambda **k: {"shifts": state["coverage"],
                                     "summary": {"total_shortfall": 2,
                                                 "total_shifts": len(state["shifts"])}})
    return frontend_client, state


def _planner_url(department="All", week="2026-08-24", date="2026-08-25", view="week"):
    return (f"/partials/planner?week_start={week}&selected_date={date}"
            f"&department={department}&view={view}")


def test_all_departments_renders_a_summary_table(planner):
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert "department-summary" in body
    assert "Emergency" in body and "Radiology" in body


def test_summary_table_uses_semantic_headers(planner):
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert '<th scope="col">Department</th>' in body
    assert 'scope="row"' in body


def test_status_is_conveyed_in_words(planner):
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert "Gap 1" in body


def test_all_departments_does_not_render_the_single_department_grid(planner):
    """The design rule: an overview, not one enormous multi-department grid."""
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert 'id="week-grid-heading"' not in body


def test_selecting_a_department_restores_the_detailed_grid(planner):
    client, _ = planner
    body = client.get(_planner_url(department="Emergency")).get_data(as_text=True)
    assert 'id="week-grid-heading"' in body
    assert "department-summary" not in body


def test_every_department_row_offers_a_drill_down(planner):
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert body.count("View department") >= 2


def test_drill_down_keeps_the_week_and_sets_the_department(planner):
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert '"department": "Emergency"' in body or '{"department":"Emergency"' in body
    assert 'name="week_start" value="2026-08-24"' in body


def test_all_departments_tab_is_offered_and_marked_active(planner):
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert "All departments" in body
    assert 'class="department-tab is-active"' in body


def test_empty_week_renders_a_truthful_state(planner):
    client, _ = planner
    body = client.get(_planner_url(week="2026-09-21", date="2026-09-21")).get_data(as_text=True)
    assert "No shifts scheduled for this week." in body
    assert "100%" not in body


def test_conflict_review_stays_available_under_all_departments(planner):
    """Conflicts are cross-department; not selecting one must not hide them."""
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert 'id="roster-status"' in body


def test_create_shift_remains_available_under_all_departments(planner):
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    assert "/partials/shifts/new" in body


def test_all_departments_view_is_loop_safe(planner):
    """Same invariant the planner has been held to since Scenario A: a "load"
    trigger is permitted only on an element that swaps its OWN contents
    (hx-target="this"). Anything else re-fires on every workspace swap and
    loops. The All-departments view must not introduce an exception."""
    import re
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    for match in re.finditer(r'<[^>]*hx-trigger="[^"]*\bload\b[^"]*"[^>]*>', body):
        assert 'hx-target="this"' in match.group(0), (
            'load trigger without hx-target="this": ' + match.group(0)[:120])


def test_summary_rows_do_not_carry_assignment_controls(planner):
    """The overview orients; rostering happens in the department grid."""
    client, _ = planner
    body = client.get(_planner_url()).get_data(as_text=True)
    table = body[body.index("department-summary"):body.index("</table>")]
    assert "/assign" not in table
    assert "/unassign" not in table


def test_gap_rail_is_hidden_when_the_summary_already_shows_it(frontend_client,
                                                              fe_api_client, monkeypatch):
    """Two copies of the same department gap list on one screen is noise."""
    monkeypatch.setattr(fe_api_client, "get_coverage",
                        lambda **k: {"shifts": [_coverage_row(1, 2, 1)]})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **k: {"staff": []})
    monkeypatch.setattr(fe_api_client, "list_shift_assignments",
                        lambda sid: {"assignments": []})
    body = frontend_client.get(
        "/partials/roster-status?week_start=2026-08-24&department=All"
    ).get_data(as_text=True)
    assert "Remaining gaps by department" not in body


def test_gap_rail_remains_for_a_single_department(frontend_client, fe_api_client,
                                                  monkeypatch):
    """With one department selected the rail is the only cross-department view."""
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **k: {"shifts": [
        {"shift_id": 1, "department": "Emergency", "shift_date": "2026-08-25",
         "required_staff_count": 2, "assigned_staff_count": 1}]})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **k: {"staff": []})
    monkeypatch.setattr(fe_api_client, "list_shift_assignments",
                        lambda sid: {"assignments": []})
    body = frontend_client.get(
        "/partials/roster-status?week_start=2026-08-24&department=Emergency"
    ).get_data(as_text=True)
    assert "Remaining gaps by department" in body


def test_conflict_review_is_never_hidden_by_the_department_selection(frontend_client,
                                                                     fe_api_client,
                                                                     monkeypatch):
    """Conflicts are cross-department and must survive either selection."""
    monkeypatch.setattr(fe_api_client, "get_coverage", lambda **k: {"shifts": []})
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **k: {"staff": []})
    for department in ("All", "Emergency"):
        body = frontend_client.get(
            f"/partials/roster-status?week_start=2026-08-24&department={department}"
        ).get_data(as_text=True)
        assert "Conflict review" in body
