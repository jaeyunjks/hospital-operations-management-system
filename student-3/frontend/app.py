#!/usr/bin/env python3
"""Student 3 pharmacy demonstration entry screen.

Run ``python3 app.py`` after initialising ``../database`` with
``python3 init_db.py --check`` and starting the Student 3 backend API.
"""

from __future__ import annotations

import os

from flask import Flask, redirect, render_template, request, session, url_for

import api_client

ROLE_MANAGER = "Pharmacy Manager"
ROLE_PHARMACIST = "Pharmacist"
ROLE_TO_DATABASE_VALUE = {
    ROLE_MANAGER: "manager",
    ROLE_PHARMACIST: "staff",
}

app = Flask(__name__)
app.secret_key = os.environ.get("HOMS_SECRET_KEY") or os.urandom(32)


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


@app.get("/")
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


@app.get("/dashboard")
def dashboard():
    """Minimal continuation page after selecting a demo identity."""
    identity = session.get("demo_identity")
    if not identity:
        return redirect(url_for("demo_entry"))
    return render_template("dashboard.html", identity=identity)


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
