#!/usr/bin/env python3
"""Student 3 pharmacy demonstration entry screen.

Run ``python3 app.py`` after initialising ``../database`` with
``python3 init_db.py --check`` and starting the Student 3 backend API.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import (Flask, redirect, render_template, request, send_from_directory,
                   session, url_for)

import api_client

ROLE_MANAGER = "Pharmacy Manager"
ROLE_PHARMACIST = "Pharmacist"
ROLE_TO_DATABASE_VALUE = {
    ROLE_MANAGER: "manager",
    ROLE_PHARMACIST: "staff",
}
BACKEND_API_URL = os.environ.get("BACKEND_API_URL", "http://localhost:5300").rstrip("/")
BASE_DIR = Path(__file__).resolve().parent
SHARED_FRONTEND_DIR = BASE_DIR.parents[1] / "shared" / "frontend"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(32)
app.config["BACKEND_API_URL"] = BACKEND_API_URL


def staff_by_role() -> dict[str, list[dict]]:
    """Group staff returned by the Student 3 backend API for the demo."""
    rows = api_client.list_staff()
    people = {ROLE_MANAGER: [], ROLE_PHARMACIST: []}
    for row in rows:
        for label, database_role in ROLE_TO_DATABASE_VALUE.items():
            if row["role"] == database_role:
                people[label].append(row)
                break
    return people


@app.before_request
def require_demo_identity():
    """Send anonymous visitors to the role picker before any application page.

    The demo routes and asset endpoints are deliberately excluded so the
    picker itself, its scripts/styles, and shared CSS cannot redirect in a
    loop.
    """
    open_endpoints = {
        "demo_entry",
        "demo_enter",
        "demo_exit",
        "static",
        "shared_assets",
        "health",
    }
    if request.endpoint in open_endpoints or request.endpoint is None:
        return None
    if session.get("demo_identity"):
        return None
    return redirect(url_for("demo_entry"))


@app.get("/demo")
def demo_entry():
    """Show the simulated-role selection screen."""
    try:
        people = staff_by_role()
        error = None
    except api_client.BackendError as exc:
        people = {ROLE_MANAGER: [], ROLE_PHARMACIST: []}
        error = f"Staff records are unavailable: {exc}"

    return render_template(
        "demo_entry.html",
        people=people,
        error=error,
        selected_role=request.args.get("role", ""),
        selected_staff_id=request.args.get("staff_id", ""),
        role_manager=ROLE_MANAGER,
        role_pharmacist=ROLE_PHARMACIST,
    )


@app.post("/demo/enter")
def demo_enter():
    """Validate and store the selected simulated pharmacy identity."""
    role = request.form.get("role", "")
    staff_id = request.form.get("staff_id", "")
    if role not in ROLE_TO_DATABASE_VALUE or not staff_id.isdigit():
        return redirect(url_for("demo_entry", role=role, staff_id=staff_id))

    try:
        person = api_client.get_staff(int(staff_id))
    except api_client.BackendError:
        person = None

    if person is None or person.get("role") != ROLE_TO_DATABASE_VALUE[role]:
        return redirect(url_for("demo_entry", role=role, staff_id=staff_id))

    session["demo_identity"] = {
        "staff_id": person["staff_id"],
        "name": person["name"],
        "role": role,
    }
    return redirect(url_for("dashboard"))


def render_page(template_name: str, title: str, note: str):
    """Render one of the intentionally empty Pharmacy Operations pages."""
    return render_template(
        template_name,
        title=title,
        note=note,
        identity=session.get("demo_identity"),
    )


@app.get("/shared/<path:filename>")
def shared_assets(filename: str):
    """Serve the team-owned shared frontend assets, matching Student 5."""
    return send_from_directory(SHARED_FRONTEND_DIR, filename)


@app.get("/")
def dashboard():
    return render_page(
        "dashboard.html",
        "Dashboard",
        "Dashboard summary content will be added here later.",
    )


@app.get("/medicines")
def medicines_list():
    return render_page(
        "medicines_list.html",
        "Medicines",
        "The medicines list will be added here later.",
    )


@app.get("/batches")
def batches():
    return render_page(
        "batches.html",
        "Batches & Expiry",
        "Batch and expiry information will be added here later.",
    )


@app.get("/movements")
def movements():
    return render_page(
        "movements.html",
        "Stock Movements",
        "Stock movement history will be added here later.",
    )


@app.get("/purchase-orders")
def purchase_orders_list():
    return render_page(
        "purchase_orders_list.html",
        "Purchase Orders",
        "The purchase orders list will be added here later.",
    )


@app.get("/suppliers")
def suppliers_list():
    return render_page(
        "suppliers_list.html",
        "Suppliers",
        "The suppliers list will be added here later.",
    )


@app.post("/demo/exit")
def demo_exit():
    session.pop("demo_identity", None)
    return redirect(url_for("demo_entry"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "student-3-frontend"}


if __name__ == "__main__":
    port = int(os.environ.get("FRONTEND_PORT", "3300"))
    app.run(host="0.0.0.0", port=port, debug=False)
