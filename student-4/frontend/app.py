"""
Frontend microservice — Room & Bed Management (Student 4).

Port 3400. Renders HTMX pages and calls the backend/API service on 5400.
It holds no business rules and never touches the database: every read
and write goes through the backend, which is what keeps the three
microservices independently replaceable.
"""

import os

from pathlib import Path

import requests
from flask import (Flask, redirect, render_template, request,
                   send_from_directory, url_for)

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent

#: The team design system, served by this service at /shared/ so the templates
#: can link it with an absolute path, exactly as the other feature
#: microservices do.
#:
#: Running from a checkout it sits two levels up at <repo>/shared/frontend.
#: In the container it is copied to /app/shared, so the image is
#: self-contained and needs no bind mount — the Dockerfile sets
#: SHARED_FRONTEND_DIR accordingly.
def _default_shared_dir():
    """Where the theme sits when SHARED_FRONTEND_DIR is not set.

    From a checkout that is <repo>/shared/frontend, two levels above this
    file. The length guard matters: in the container BASE_DIR is /app, which
    has only one parent, so indexing blindly would raise IndexError before
    the environment variable could ever be read.
    """
    parents = BASE_DIR.parents
    root = parents[1] if len(parents) > 1 else BASE_DIR
    return root / "shared" / "frontend"


SHARED_FRONTEND_DIR = Path(os.getenv("SHARED_FRONTEND_DIR") or _default_shared_dir())

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5400")
BACKEND_TIMEOUT = int(os.getenv("BACKEND_TIMEOUT", "40"))
PORT = int(os.getenv("PORT", "3400"))

CARE_CATEGORIES = ("Surgical", "Short-term", "Long-term")
ROOM_STATUSES = ("Available", "In Use", "Cleaning", "Out of Service")
BED_STATUSES = ("available", "reserved", "occupied", "maintenance")
ARRANGEMENT_STATUSES = ("Scheduled", "In Progress", "Completed", "Cancelled")
URGENCIES = ("Low", "Medium", "High", "Critical")
SHORTAGE_STATUSES = ("Open", "Option offered", "Resolved", "Escalated", "Cancelled")


def api(method, path, **kwargs):
    """Call the backend and unwrap the response envelope.

    Returns (data, error_message). The templates render whichever is
    set, so a backend outage shows a banner instead of a stack trace.
    """
    try:
        response = requests.request(
            method, BACKEND_URL + path, timeout=BACKEND_TIMEOUT, **kwargs
        )
    except requests.RequestException as error:
        return None, "Cannot reach the Room & Bed API: {}".format(error)

    try:
        body = response.json()
    except ValueError:
        return None, "API returned an unreadable response ({})".format(response.status_code)

    if not body.get("success"):
        return None, body.get("error") or "Request failed"
    return body["data"], None


@app.context_processor
def template_globals():
    return {
        "care_categories": CARE_CATEGORIES,
        "room_statuses": ROOM_STATUSES,
        "bed_statuses": BED_STATUSES,
        "arrangement_statuses": ARRANGEMENT_STATUSES,
        "urgencies": URGENCIES,
        "shortage_statuses": SHORTAGE_STATUSES,
    }


# ---------------------------------------------------------------------
# Dashboard — availability and status at a glance
# ---------------------------------------------------------------------
@app.get("/")
def dashboard():
    beds, error = api("GET", "/api/rooms/availability", params={
        "care_category": request.args.get("care_category") or None,
        "bed_status": request.args.get("bed_status") or None,
        "ward": request.args.get("ward") or None,
    })
    board, _ = api("GET", "/api/theatres/board")
    template = "partials/bed_table.html" if request.headers.get("HX-Request") else "dashboard.html"
    return render_template(
        template,
        beds=beds or [],
        error=error,
        theatre_summary=(board or {}).get("summary"),
        filters=request.args,
    )


@app.post("/occupancy-summary")
def occupancy_summary():
    """AI panel. Rendered on demand so the page never waits on the model."""
    result, error = api("POST", "/api/rooms/occupancy-summary", json={})
    return render_template("partials/occupancy_summary.html", result=result, error=error)


# ---------------------------------------------------------------------
# Rooms — CRUD
# ---------------------------------------------------------------------
@app.get("/rooms")
def rooms():
    records, error = api("GET", "/api/rooms", params={
        "ward": request.args.get("ward") or None,
        "status": request.args.get("status") or None,
    })
    types, _ = api("GET", "/api/room-types")
    return render_template("rooms.html", rooms=records or [], room_types=types or [],
                           error=error, filters=request.args)


@app.post("/rooms")
def create_room():
    _, error = api("POST", "/api/rooms", json={
        "room_number": request.form["room_number"].strip(),
        "ward": request.form["ward"].strip(),
        "floor": request.form["floor"].strip(),
        "type_id": int(request.form["type_id"]),
        "notes": request.form.get("notes") or None,
    })
    return redirect(url_for("rooms", message=error or "Room created"))


@app.post("/rooms/<int:room_id>/status")
def set_room_status(room_id):
    _, error = api("PUT", "/api/rooms/{}/status".format(room_id),
                   json={"status": request.form["status"]})
    return redirect(url_for("rooms", message=error or "Room status updated"))


@app.post("/rooms/<int:room_id>/retire")
def retire_room(room_id):
    _, error = api("DELETE", "/api/rooms/{}".format(room_id))
    return redirect(url_for("rooms", message=error or "Room retired"))


# ---------------------------------------------------------------------
# Operating theatre board
# ---------------------------------------------------------------------
@app.get("/theatres")
def theatres():
    board, error = api("GET", "/api/theatres/board")
    return render_template("theatres.html", board=board, error=error)


# ---------------------------------------------------------------------
# Arrangements — book, release, transfer
# ---------------------------------------------------------------------
@app.get("/arrangements")
def arrangements():
    records, error = api("GET", "/api/arrangements", params={
        "status": request.args.get("status") or None,
        "purpose": request.args.get("purpose") or None,
    })
    free_beds, _ = api("GET", "/api/rooms/availability", params={"bed_status": "available"})
    return render_template("arrangements.html", arrangements=records or [],
                           free_beds=free_beds or [], error=error, filters=request.args)


@app.post("/arrangements")
def create_arrangement():
    payload = {
        "bed_id": int(request.form["bed_id"]),
        "patient_id": int(request.form["patient_id"]),
        "purpose": request.form["purpose"],
        "start_time": request.form["start_time"].replace("T", " "),
        "patient_requirements": request.form.get("patient_requirements") or None,
        "status": request.form.get("status", "Scheduled"),
    }
    if request.form.get("end_time"):
        payload["end_time"] = request.form["end_time"].replace("T", " ")
    if payload["purpose"] == "Surgery":
        payload["procedure_name"] = request.form.get("procedure_name")
        payload["surgeon_name"] = request.form.get("surgeon_name")

    _, error = api("POST", "/api/arrangements", json=payload)
    return redirect(url_for("arrangements", message=error or "Arrangement created"))


@app.post("/arrangements/<int:arrangement_id>/release")
def release(arrangement_id):
    _, error = api("PUT", "/api/arrangements/{}/release".format(arrangement_id), json={})
    return redirect(url_for("arrangements", message=error or "Released"))


@app.post("/arrangements/<int:arrangement_id>/cancel")
def cancel(arrangement_id):
    _, error = api("PUT", "/api/arrangements/{}/cancel".format(arrangement_id),
                   json={"reason": request.form.get("reason")})
    return redirect(url_for("arrangements", message=error or "Cancelled"))


@app.post("/arrangements/<int:arrangement_id>/transfer")
def transfer(arrangement_id):
    _, error = api("POST", "/api/arrangements/{}/transfer".format(arrangement_id),
                   json={"to_bed_id": int(request.form["to_bed_id"]),
                         "reason": request.form.get("reason")})
    return redirect(url_for("arrangements", message=error or "Patient transferred"))


# ---------------------------------------------------------------------
# AI suggestion
# ---------------------------------------------------------------------
@app.post("/suggest")
def suggest():
    result, error = api("POST", "/api/rooms/suggest", json={
        "patient_requirements": request.form.get("patient_requirements", ""),
        "ward": request.form.get("ward") or None,
    })
    return render_template("partials/suggestions.html", result=result, error=error)


# ---------------------------------------------------------------------
# Shortage cases
# ---------------------------------------------------------------------
@app.get("/shortages")
def shortages():
    cases, error = api("GET", "/api/shortage-cases",
                       params={"status": request.args.get("status") or None})
    return render_template("shortages.html", cases=cases or [], error=error,
                           filters=request.args)


@app.post("/shortages")
def open_shortage():
    _, error = api("POST", "/api/shortage-cases", json={
        "patient_id": int(request.form["patient_id"]),
        "required_care_category": request.form["required_care_category"],
        "required_ward": request.form.get("required_ward") or None,
        "urgency": request.form.get("urgency", "Medium"),
        "holding_location": request.form.get("holding_location") or None,
    })
    return redirect(url_for("shortages", message=error or "Shortage case opened"))


@app.get("/shortages/<int:case_id>")
def shortage_detail(case_id):
    detail, error = api("GET", "/api/shortage-cases/{}/options".format(case_id))
    return render_template("shortage_detail.html", detail=detail, error=error)


@app.post("/shortages/<int:case_id>/decide")
def decide_shortage(case_id):
    payload = {
        "chosen_option": request.form["chosen_option"],
        "decision_reason": request.form.get("decision_reason", ""),
    }
    if request.form.get("resolved_bed_id"):
        payload["resolved_bed_id"] = int(request.form["resolved_bed_id"])
    if request.form.get("escalate"):
        payload["escalate"] = True

    _, error = api("PUT", "/api/shortage-cases/{}/decide".format(case_id), json=payload)
    return redirect(url_for("shortage_detail", case_id=case_id,
                            message=error or "Decision recorded"))


@app.get("/shared/<path:filename>")
def shared_assets(filename):
    """Serve shared/frontend so pages can link /shared/css/main.css.

    Kept out of Flask's own static folder because the shared theme is owned
    by the team, not by this feature.
    """
    return send_from_directory(SHARED_FRONTEND_DIR, filename)


@app.get("/health")
def health():
    backend, error = api("GET", "/health")
    return {"service": "student-4-frontend", "port": PORT,
            "backend": backend, "error": error}


if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "0.0.0.0"), port=PORT,
            debug=os.getenv("FLASK_DEBUG", "1") == "1")
