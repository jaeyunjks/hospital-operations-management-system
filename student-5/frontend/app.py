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

from flask import Flask, render_template, request, send_from_directory  # noqa: E402

import api_client  # noqa: E402
from api_client import BackendError, BackendUnavailableError  # noqa: E402

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
        except (BackendUnavailableError, BackendError):
            # The shell still renders; the table partial reports the failure.
            departments, roles = [], []

        return render_template(
            "staff_directory.html", today=_today(), active="staff",
            departments=departments, roles=roles,
            staff=None, error=None,
        )

    @app.get("/partials/staff-table")
    def staff_table_partial():
        query = (request.args.get("q") or "").strip()
        department = request.args.get("department") or None
        role = request.args.get("role") or None
        availability = request.args.get("availability_status") or None
        filtered = bool(query or department or role or availability)

        try:
            # /api/staff/search rejects a blank q, so only use it when the
            # user actually typed something; otherwise the plain list
            # endpoint already applies the same three filters.
            if query:
                data = api_client.search_staff(
                    query=query, department=department, role=role,
                    availability_status=availability)
            else:
                data = api_client.list_staff(
                    availability_status=availability, department=department,
                    role=role)
        except (BackendUnavailableError, BackendError) as error:
            return render_template("partials/staff_table.html",
                                    staff=None, filtered=filtered, error=str(error))

        return render_template("partials/staff_table.html",
                                staff=data["staff"], filtered=filtered, error=None)

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
