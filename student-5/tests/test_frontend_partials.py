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
    assert "No staff match these filters" in body


def test_staff_table_empty_dataset_state(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff", lambda **kw: {"count": 0, "staff": []})
    body = frontend_client.get("/partials/staff-table").data.decode()
    assert "No staff records" in body


def test_staff_table_backend_unavailable(frontend_client, fe_api_client, monkeypatch):
    monkeypatch.setattr(fe_api_client, "list_staff", _raise_unavailable)
    response = frontend_client.get("/partials/staff-table")
    assert response.status_code == 200
    assert "Staff records unavailable" in response.data.decode()
