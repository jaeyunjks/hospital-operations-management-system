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


@pytest.fixture
def frontend_client(fe_api_client):
    frontend_app_module = _load_frontend_app_module()
    app = frontend_app_module.create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
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
    for forbidden in ("qualification", "bank contract", "band 5", "band 6",
                      "weekly availability", "confidence", "suitability"):
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


def test_new_shift_form_uses_selected_date(frontend_client):
    body = frontend_client.get(
        "/partials/shifts/new?shift_date=2026-08-29").data.decode()
    assert "Create shift" in body
    assert 'value="2026-08-29"' in body
    assert 'min="1"' in body


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


def test_create_shift_surfaces_backend_validation(
        frontend_client, fe_api_client, monkeypatch):
    import api_client
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
    monkeypatch.setattr(fe_api_client, "get_shift", lambda sid: {"shift": SHIFT_FIXTURE})
    form = frontend_client.get("/partials/shifts/11/edit").data.decode()
    assert "Edit shift" in form and "Night shift." in form

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
         "department": "Emergency", "assignment_status": "Assigned"},
        {"staff_id": 12, "name": "Chloe Bennett", "role": "Registered Nurse",
         "department": "Intensive Care", "assignment_status": "Cancelled"},
    ]
    candidates = [
        {"staff_id": 1, "name": "Amara Okafor", "role": "Registered Nurse",
         "department": "Emergency", "availability_status": "Available",
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
    assert "deterministic filtering, not an AI recommendation" in body


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
    template = (
        FRONTEND_DIR / "templates" / "partials" / "planner_workspace.html"
    ).read_text()
    assert 'hx-trigger="load' not in template
    assert "fetch(" not in template
