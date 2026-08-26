#!/usr/bin/env python3
"""Flask frontend microservice for Student 5 — Staff & Shift Management.

Workforce Overview only (first vertical slice). Renders the page shell
immediately, then HTMX requests populate it from the backend/API
microservice via server-side calls in api_client.py:

    Browser --HTMX--> this Flask app --HTTP--> backend/API (port 5500)
                                                       |
                                                       v
                                            database service (port 6500)

The browser never calls the backend directly, so no CORS configuration is
needed anywhere in this stack.

Usage:
    python3 app.py                    # serves on port 3500
    FRONTEND_PORT=4000 python3 app.py
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import (Flask, make_response, render_template,  # noqa: E402
                   request, send_from_directory)

import api_client  # noqa: E402
from api_client import (BackendError, BackendUnavailableError,  # noqa: E402
                        NotFoundError)

DEFAULT_PORT = 3500
BASE_DIR = Path(__file__).resolve().parent
SHARED_FRONTEND_DIR = BASE_DIR.parents[1] / "shared" / "frontend"


def _today() -> str:
    """ISO date — used for API filters and machine-readable display."""
    return datetime.date.today().isoformat()


def _today_long() -> str:
    """Human-readable date for the page header (e.g. "Wed, 26 Aug 2026")."""
    # %-d is POSIX-only but this service targets Linux/macOS containers;
    # lstrip keeps it correct without relying on the platform extension.
    today = datetime.date.today()
    return today.strftime("%a, %d %b %Y").replace(" 0", " ")


def _display_id(staff_id: int) -> str:
    """Deterministic display identifier. The real numeric primary key is what
    every API call uses; this is presentation only and is not an external HR
    identifier."""
    return "S-%03d" % staff_id


def _shift_display_id(shift_id: int) -> str:
    """Deterministic presentation ID; API calls still use the integer key."""
    return "SH-%03d" % shift_id


def _valid_iso_date(value: str) -> bool:
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _date_long(value: str) -> str:
    """Format an ISO date without losing the stored value on bad input."""
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
        return parsed.strftime("%a, %d %b %Y").replace(" 0", " ")
    except (TypeError, ValueError):
        return value


def _coverage(assigned: int, required: int):
    """Return display text and badge class from real assignment counts."""
    difference = assigned - required
    if difference == 0:
        return {"label": "Fully staffed", "badge": "success", "difference": 0}
    if difference < 0:
        return {"label": f"Gap {abs(difference)}", "badge": "warning",
                "difference": difference}
    return {"label": f"Overstaffed by {difference}", "badge": "info",
            "difference": difference}


def _format_timestamp(value):
    """Render a stored 'YYYY-MM-DD HH:MM:SS' timestamp for people.

    Returns the raw value unchanged if it is not in the expected shape, so a
    formatting surprise can never blank out a real stored value.
    """
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(value, fmt).strftime("%d %b %Y · %H:%M")
        except (ValueError, TypeError):
            continue
    return value


def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p[:1].isalpha()]
    if not parts:
        return "?"
    return (parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "")).upper()


#: Assignment states that no longer occupy a place on a shift.
_INACTIVE_ASSIGNMENT = ("Cancelled", "Declined")


def _split_shifts(shifts):
    """Split assigned shifts into (current, upcoming-within-7-days).

    "Current" means today's date and the clock is inside the shift window,
    including windows that cross midnight. Everything else that starts from
    today onward and within seven days is upcoming. Derived entirely from
    real shift_date/start_time/end_time — nothing is inferred beyond this.
    """
    today = datetime.date.today()
    now = datetime.datetime.now().strftime("%H:%M")
    horizon = today + datetime.timedelta(days=7)

    current, upcoming = None, []
    for shift in shifts:
        if shift.get("assignment_status") in _INACTIVE_ASSIGNMENT:
            continue
        try:
            shift_date = datetime.datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
        except (ValueError, KeyError, TypeError):
            continue

        start, end = shift.get("start_time", ""), shift.get("end_time", "")
        overnight = bool(start and end and end < start)
        in_window = (start <= now < end) if not overnight else (now >= start or now < end)

        if shift_date == today and in_window and current is None:
            current = shift
        elif today <= shift_date <= horizon:
            upcoming.append(shift)

    upcoming.sort(key=lambda s: (s.get("shift_date", ""), s.get("start_time", "")))
    return current, upcoming


def create_app() -> Flask:
    # Absolute template/static paths: Flask's default resolution relies on
    # how the module was imported, which breaks when app.py is loaded via
    # importlib (as the test suite does) rather than a normal `import app`.
    app = Flask(
        __name__,
        static_folder=str(BASE_DIR / "static"),
        static_url_path="/static",
        template_folder=str(BASE_DIR / "templates"),
    )

    # Display helpers shared by the table and the drawer. Presentation only —
    # every API call still uses the real numeric primary key.
    app.jinja_env.globals["display_id"] = _display_id
    app.jinja_env.globals["shift_display_id"] = _shift_display_id
    app.jinja_env.globals["initials"] = _initials
    app.jinja_env.globals["date_long"] = _date_long

    # ---------------------------------------------------------- shared assets
    @app.get("/shared/<path:filename>")
    def shared_assets(filename: str):
        """Serve shared/frontend so this page can link /shared/css/main.css.

        Kept separate from Flask's own static folder because the shared
        design system lives outside this microservice's directory and is
        owned by the whole team, not by Student 5.
        """
        return send_from_directory(SHARED_FRONTEND_DIR, filename)

    # ------------------------------------------------------------------ health
    @app.get("/health")
    def health():
        try:
            backend = api_client.get_health()
            status = "ok"
        except (BackendUnavailableError, BackendError) as error:
            backend, status = {"message": str(error)}, "unavailable"

        return {
            "status": "ok" if status == "ok" else "degraded",
            "service": "student-5-frontend",
            "feature": "Staff & Shift Management",
            "backend_service": {"url": api_client.API_BASE_URL, "status": status,
                                "detail": backend},
        }, (200 if status == "ok" else 503)

    # -------------------------------------------------------------------- page
    @app.get("/")
    def workforce_overview():
        """Render the page shell. No backend call here — HTMX fetches the
        operational content once the shell is on screen, so the page is
        usable immediately even if the backend is down.

        kpis/shifts/summary are explicitly None (not merely absent) so the
        included partials render their loading skeleton rather than a blank
        or Undefined value on first paint.
        """
        return render_template(
            "workforce_overview.html", today=_today(), today_long=_today_long(),
            active="workforce",
            kpis=None, shifts=None, top_gap=None, summary=None, error=None,
        )

    # --------------------------------------------------------- staff directory
    @app.get("/staff")
    def staff_directory():
        """Staff Directory shell. Filter options are derived from the real
        staff records the service returns — never a hard-coded list."""
        try:
            records = api_client.list_staff()["staff"]
            departments = sorted({r["department"] for r in records if r.get("department")})
            roles = sorted({r["role"] for r in records if r.get("role")})
            employments = sorted({r["employment_status"] for r in records
                                   if r.get("employment_status")})
        except (BackendUnavailableError, BackendError):
            # The shell still renders; the table partial reports the failure.
            departments, roles, employments = [], [], []

        return render_template(
            "staff_directory.html", today=_today(), active="staff",
            departments=departments, roles=roles, employments=employments,
            staff=None, error=None,
        )

    # ------------------------------------------------------------ shift planner
    @app.get("/shifts")
    def shift_planner():
        """Render the planner shell and real filter choices.

        The operational list itself is loaded by one HTMX request so a backend
        failure remains scoped to that region. The all-shifts request here is
        used only to derive filter options; date-scoped data is never filtered
        in browser JavaScript.
        """
        selected_date = request.args.get("date") or _today()
        if not _valid_iso_date(selected_date):
            selected_date = _today()

        service_available = True
        try:
            records = api_client.list_shifts()["shifts"]
            departments = sorted({row["department"] for row in records
                                  if row.get("department")})
            roles = sorted({row["required_role"] for row in records
                            if row.get("required_role")})
        except (BackendUnavailableError, BackendError):
            departments, roles, service_available = [], [], False

        return render_template(
            "shift_planner.html", today=_today(), active="shifts",
            selected_date=selected_date, departments=departments, roles=roles,
            statuses=api_client.SHIFT_STATUSES,
            service_available=service_available,
        )

    @app.get("/partials/shifts")
    def shifts_partial():
        selected_date = request.args.get("shift_date") or _today()
        department = request.args.get("department") or None
        required_role = request.args.get("required_role") or None
        shift_status = request.args.get("shift_status") or None
        filtered = bool(department or required_role or shift_status)

        try:
            shift_data = api_client.list_shifts(
                shift_date=selected_date, department=department,
                shift_status=shift_status)
            coverage_data = api_client.get_coverage(
                shift_date=selected_date, department=department,
                shift_status=shift_status)
        except (BackendUnavailableError, BackendError) as error:
            return render_template(
                "partials/shift_list.html", shifts=None, error=str(error),
                filtered=filtered, selected_date=selected_date)

        coverage_by_id = {row["shift_id"]: row for row in coverage_data["shifts"]}
        shifts = []
        for shift in shift_data["shifts"]:
            if required_role and shift.get("required_role") != required_role:
                continue
            row = dict(shift)
            counts = coverage_by_id.get(shift["shift_id"], {})
            assigned = int(counts.get("assigned_staff_count", 0))
            required = int(shift["required_staff_count"])
            row["assigned_staff_count"] = assigned
            row["coverage"] = _coverage(assigned, required)
            shifts.append(row)

        return render_template(
            "partials/shift_list.html", shifts=shifts, error=None,
            filtered=filtered, selected_date=selected_date)

    def _render_shift_detail(shift_id, notice=None):
        try:
            shift = api_client.get_shift(shift_id)["shift"]
        except NotFoundError:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=True, error=None, notice=None)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=False, error=str(error), notice=None)

        assignments, assignment_error = None, None
        try:
            records = api_client.list_shift_assignments(shift_id)["assignments"]
            assignments = [row for row in records
                           if row.get("assignment_status") not in _INACTIVE_ASSIGNMENT]
        except (BackendUnavailableError, BackendError) as error:
            assignment_error = str(error)

        candidates, candidate_error = [], None
        if assignments is None:
            candidate_error = (
                "Candidate assignment is unavailable until current assignments can be loaded.")
        else:
            try:
                candidate_records = api_client.list_staff(
                    role=shift["required_role"], availability_status="Available")["staff"]
                active_ids = {row["staff_id"] for row in assignments}
                candidates = [row for row in candidate_records
                              if row["staff_id"] not in active_ids]
                candidates.sort(key=lambda row: (
                    row.get("department") != shift["department"], row.get("name", "")))
            except (BackendUnavailableError, BackendError) as error:
                candidate_error = str(error)

        assigned_count = len(assignments) if assignments is not None else None
        coverage = (_coverage(assigned_count, int(shift["required_staff_count"]))
                    if assigned_count is not None else None)
        return render_template(
            "partials/shift_detail.html", shift=shift, not_found=False,
            error=None, notice=notice, assignments=assignments,
            assignment_error=assignment_error, candidates=candidates,
            candidate_error=candidate_error, coverage=coverage,
            assigned_count=assigned_count,
        )

    @app.get("/partials/shifts/<int:shift_id>")
    def shift_detail_partial(shift_id: int):
        return _render_shift_detail(shift_id)

    def _shift_form_values(source):
        return {
            "department": (source.get("department") or "").strip(),
            "shift_date": (source.get("shift_date") or "").strip(),
            "start_time": (source.get("start_time") or "").strip(),
            "end_time": (source.get("end_time") or "").strip(),
            "required_role": (source.get("required_role") or "").strip(),
            "required_staff_count": source.get("required_staff_count", 1),
            "shift_status": (source.get("shift_status") or "Planned").strip(),
            "notes": (source.get("notes") or "").strip(),
        }

    def _shift_payload(values):
        try:
            count = int(values["required_staff_count"])
        except (TypeError, ValueError):
            raise ValueError("Required staff count must be a whole number greater than zero.")
        if count < 1:
            raise ValueError("Required staff count must be greater than zero.")
        return {**values, "required_staff_count": count,
                "notes": values["notes"] or None}

    def _render_shift_form(values, mode, shift_id=None, error=None):
        return render_template(
            "partials/shift_form.html", values=values, mode=mode,
            shift_id=shift_id, statuses=api_client.SHIFT_STATUSES, error=error)

    @app.get("/partials/shifts/new")
    def new_shift_partial():
        selected_date = request.args.get("shift_date") or _today()
        if not _valid_iso_date(selected_date):
            selected_date = _today()
        return _render_shift_form({
            "department": "", "shift_date": selected_date,
            "start_time": "07:00", "end_time": "15:00",
            "required_role": "", "required_staff_count": 1,
            "shift_status": "Planned", "notes": "",
        }, "create")

    @app.get("/partials/shifts/<int:shift_id>/edit")
    def edit_shift_partial(shift_id: int):
        try:
            values = _shift_form_values(api_client.get_shift(shift_id)["shift"])
        except NotFoundError:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=True, error=None, notice=None)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=False, error=str(error), notice=None)
        return _render_shift_form(values, "edit", shift_id=shift_id)

    @app.post("/partials/shifts")
    def create_shift_partial():
        values = _shift_form_values(request.form)
        try:
            payload = _shift_payload(values)
            created = api_client.create_shift(payload)["shift"]
        except ValueError as error:
            return _render_shift_form(values, "create", error=str(error))
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_form(values, "create", error=str(error))

        response = make_response(_render_shift_detail(
            created["shift_id"], notice={"kind": "success", "text": "Shift created."}))
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    @app.post("/partials/shifts/<int:shift_id>")
    def update_shift_partial(shift_id: int):
        values = _shift_form_values(request.form)
        try:
            api_client.update_shift(shift_id, _shift_payload(values))
        except ValueError as error:
            return _render_shift_form(values, "edit", shift_id=shift_id, error=str(error))
        except NotFoundError:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=True, error=None, notice=None)
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_form(values, "edit", shift_id=shift_id, error=str(error))

        response = make_response(_render_shift_detail(
            shift_id, notice={"kind": "success", "text": "Shift updated."}))
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    @app.post("/partials/shifts/<int:shift_id>/delete")
    def delete_shift_partial(shift_id: int):
        try:
            api_client.delete_shift(shift_id)
        except NotFoundError:
            return render_template("partials/shift_detail.html", shift=None,
                                   not_found=True, error=None, notice=None)
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Shift was not deleted. " + str(error)})

        body = render_template("partials/shift_action_result.html",
                               title="Shift deleted",
                               message="The shift and its assignment records were permanently deleted.")
        response = make_response(body)
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    def _staff_id_from_form():
        try:
            return int(request.form.get("staff_id", ""))
        except (TypeError, ValueError):
            return None

    @app.post("/partials/shifts/<int:shift_id>/assign")
    def assign_shift_staff_partial(shift_id: int):
        staff_id = _staff_id_from_form()
        if staff_id is None:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Select a valid staff member."})
        try:
            api_client.assign_staff(shift_id, staff_id)
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Staff was not assigned. " + str(error)})
        response = make_response(_render_shift_detail(
            shift_id, notice={"kind": "success", "text": "Staff assigned to shift."}))
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    @app.post("/partials/shifts/<int:shift_id>/unassign")
    def unassign_shift_staff_partial(shift_id: int):
        staff_id = _staff_id_from_form()
        if staff_id is None:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Select a valid staff member."})
        try:
            api_client.unassign_staff(shift_id, staff_id)
        except (BackendUnavailableError, BackendError) as error:
            return _render_shift_detail(
                shift_id, notice={"kind": "danger", "text": "Staff was not unassigned. " + str(error)})
        response = make_response(_render_shift_detail(
            shift_id, notice={"kind": "success", "text": "Staff unassigned from shift."}))
        response.headers["HX-Trigger"] = "shifts-updated"
        return response

    @app.get("/partials/staff-table")
    def staff_table_partial():
        query = (request.args.get("q") or "").strip()
        department = request.args.get("department") or None
        role = request.args.get("role") or None
        availability = request.args.get("availability_status") or None
        employment = request.args.get("employment_status") or None
        filtered = bool(query or department or role or availability or employment)

        try:
            # /api/staff/search rejects a blank q, so only use it when the
            # user actually typed something; otherwise the plain list
            # endpoint already applies the same three filters.
            if query:
                data = api_client.search_staff(
                    query=query, department=department, role=role,
                    availability_status=availability,
                    employment_status=employment)
            else:
                data = api_client.list_staff(
                    availability_status=availability, department=department,
                    role=role, employment_status=employment)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/staff_table.html",
                                    staff=None, filtered=filtered, error=str(error))

        return render_template("partials/staff_table.html",
                                staff=data["staff"], filtered=filtered, error=None)

    def _render_detail(staff_id, notice=None, status_code=200):
        """Render the drawer for one staff member.

        Shift data is fetched separately: if that call fails the staff record
        still renders, with the assignment sections marked unavailable rather
        than taking the whole drawer down.
        """
        try:
            record = api_client.get_staff(staff_id)["staff"]
        except NotFoundError:
            return render_template("partials/staff_detail.html",
                                    person=None, error=None, notice=None,
                                    not_found=True), 404
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/staff_detail.html",
                                    person=None, error=str(error), notice=None), status_code

        current, upcoming, shifts_error = None, [], None
        try:
            current, upcoming = _split_shifts(
                api_client.list_staff_shifts(staff_id)["shifts"])
        except (BackendUnavailableError, BackendError) as error:
            shifts_error = str(error)

        return render_template(
            "partials/staff_detail.html", person=record, error=None,
            notice=notice, statuses=api_client.AVAILABILITY_STATUSES,
            display_id=_display_id(staff_id), initials=_initials(record["name"]),
            last_updated=_format_timestamp(record.get("updated_at")),
            current_assignment=current, upcoming_shifts=upcoming,
            shifts_error=shifts_error,
        ), status_code

    @app.get("/partials/staff/<int:staff_id>")
    def staff_detail_partial(staff_id: int):
        """Drawer contents for one staff member: read-only reference data,
        derived assignment context, and the operational availability control."""
        return _render_detail(staff_id)

    @app.post("/partials/staff/<int:staff_id>/availability")
    def staff_availability_update(staff_id: int):
        """Apply an availability change, then re-render the drawer.

        Availability is operational scheduling state owned by HOMS. Employee
        reference attributes are never submitted here.
        """
        status = (request.form.get("availability_status") or "").strip()

        if status not in api_client.AVAILABILITY_STATUSES:
            return _render_detail(
                staff_id, status_code=400,
                notice={"kind": "danger",
                        "text": "Select a valid availability status."})

        try:
            api_client.update_availability(staff_id, status)
        except (BackendUnavailableError, BackendError) as error:
            return _render_detail(
                staff_id,
                notice={"kind": "danger",
                        "text": "Availability was not updated. " + str(error)})

        body, code = _render_detail(
            staff_id,
            notice={"kind": "success",
                    "text": "Availability updated to " + status + "."})
        # Tell the directory table to re-query so its badge matches the drawer.
        # Emitted only on success, so a failed save never implies a change.
        response = make_response(body, code)
        response.headers["HX-Trigger"] = "staff-updated"
        return response

    # ---------------------------------------------------------------- partials
    @app.get("/partials/kpis")
    def kpis_partial():
        today = request.args.get("date") or _today()
        try:
            coverage = api_client.get_coverage(shift_date=today)
            roster = api_client.list_staff()
            available = api_client.list_staff(availability_status="Available")
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/kpis.html", today=today,
                                    kpis=None, error=str(error))

        shifts = coverage["shifts"]
        total_required = sum(row["required_staff_count"] for row in shifts)
        total_assigned = sum(row["assigned_staff_count"] for row in shifts)
        coverage_pct = round(total_assigned / total_required * 100) if total_required else None

        kpis = {
            "coverage_pct": coverage_pct,
            "gap": coverage["summary"]["total_shortfall"],
            "roster_total": roster["count"],
            "available_count": available["count"],
            "shifts_today": coverage["summary"]["total_shifts"],
        }
        return render_template("partials/kpis.html", today=today, kpis=kpis, error=None)

    @app.get("/partials/demand")
    def demand_partial():
        today = request.args.get("date") or _today()
        try:
            coverage = api_client.get_coverage(shift_date=today)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/demand.html", today=today,
                                    shifts=None, top_gap=None, error=str(error))

        shifts = coverage["shifts"]
        gaps = [row for row in shifts if row["shortfall"] > 0]
        top_gap = max(gaps, key=lambda row: row["shortfall"]) if gaps else None
        return render_template("partials/demand.html", today=today,
                                shifts=shifts, top_gap=top_gap, error=None)

    @app.get("/partials/summary")
    def summary_partial():
        today = request.args.get("date") or _today()
        try:
            summary = api_client.get_coverage_summary(shift_date=today)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/summary.html", today=today,
                                    summary=None, error=str(error))
        return render_template("partials/summary.html", today=today,
                                summary=summary, error=None)

    return app


if __name__ == "__main__":
    port = int(os.environ.get("FRONTEND_PORT", DEFAULT_PORT))
    create_app().run(host="0.0.0.0", port=port, debug=False)
