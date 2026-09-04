"""
app.py - Flask frontend for the Clinical Staff Management feature.

WHAT THIS IS
------------
A thin server-rendered UI. Every route does the same three things:

    1. work out which role is "logged in" (temporary stand-in, see below),
    2. call the backend REST API (port 5200) with `requests`,
    3. hand the JSON it gets back straight to a Jinja template.

There is deliberately NO business logic here: no database access, no
role-based filtering of records, no workflow rules. The backend already
returns only what the current role is entitled to see (it scopes every
list by assignment/role), so the templates just render whatever arrives.
If a view looks different for a Doctor vs a Nurse vs a Specialist, that is
because the backend sent different JSON - not because this file decided so.

ROLE STAND-IN (temporary)
-------------------------
Real shared authentication is owned by another team and is not built yet.
Until it lands we use the team's agreed stand-in:

  * the active role is chosen with a `?role=doctor|nurse|specialist` query
    parameter (there is also a dropdown in the base template that just sets
    that parameter),
  * the choice is remembered in a plain cookie so you don't have to append
    it to every link,
  * on every backend call we forward it as the `X-User-Role` header.

That header is the single seam the real auth component will replace: when
it arrives, the backend reads identity from it instead of from a role
string, and this file keeps sending one header - nothing else changes.

CONFIG
------
BACKEND_API_URL - base URL of the Clinical Staff Management backend.
                  Defaults to localhost:5200 for local dev; override with
                  an env var in Docker/compose.

This frontend binds port 3200; the backend it calls is on 5200.
"""

import os

import requests
from flask import (
    Flask,
    make_response,
    render_template,
    request,
)

app = Flask(__name__)

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
# Backend base URL. Overridable for Docker/deployment; localhost for dev.
BACKEND_API_URL = os.environ.get(
    "BACKEND_API_URL", "http://localhost:5200"
).rstrip("/")

# How long each backend call may take (connect + read), in seconds.
BACKEND_TIMEOUT = 10

# The only roles this feature serves. Anything else falls back to the first.
CLINICAL_ROLES = ("doctor", "nurse", "specialist")
DEFAULT_ROLE = CLINICAL_ROLES[0]

# Cookie the chosen role is remembered in between requests.
ROLE_COOKIE = "csm_role"


# ============================================================
# Role stand-in helpers
# ============================================================
def current_role():
    """
    The role acting as "logged in" for this request.

    Priority: ?role= query parameter, then the remembered cookie, then the
    default. An unrecognised value is ignored rather than passed through.
    """
    picked = request.args.get("role") or request.cookies.get(ROLE_COOKIE)
    return picked if picked in CLINICAL_ROLES else DEFAULT_ROLE


def _remember_role(response):
    """Persist the current role in a cookie so links don't all need ?role=."""
    response.set_cookie(ROLE_COOKIE, current_role(), samesite="Lax")
    return response


# ============================================================
# Backend calls
# All of them go through here so the role header and error shape are
# applied in exactly one place.
# ============================================================
def _backend(method, path, **kwargs):
    """
    Call the backend API once and return (data, error).

    `data`  - parsed JSON on success (dict/list), else None.
    `error` - None on success, otherwise a short string for the template
              to show. We never raise into the view; a down backend should
              render a page with a message, not a 500.
    """
    url = "{}{}".format(BACKEND_API_URL, path)
    # Forward the stand-in identity. Real auth will read this same header.
    headers = {"X-User-Role": current_role()}

    try:
        resp = requests.request(
            method, url, headers=headers, timeout=BACKEND_TIMEOUT, **kwargs
        )
    except requests.RequestException as exc:
        return None, "Could not reach the backend: {}".format(exc)

    # Parse the body if there is one; the backend always speaks JSON.
    try:
        data = resp.json() if resp.content else None
    except ValueError:
        data = None

    if not resp.ok:
        # Pass the backend's own error message through if it gave one.
        message = None
        if isinstance(data, dict):
            message = data.get("error")
        return data, message or "Backend returned {}".format(resp.status_code)

    return data, None


def backend_get(path, params=None):
    """GET helper - most read-only pages use this."""
    return _backend("GET", path, params=params)


def backend_post(path, json_body=None):
    """POST helper - form submissions use this."""
    return _backend("POST", path, json=json_body)


# ============================================================
# Template rendering
# ============================================================
def _render(template_name, data, error):
    """
    Render `template_name`, passing the backend JSON straight through as
    `data`, plus the bits every page needs: the active role, the list of
    roles for the dropdown, and any backend error string.
    """
    response = make_response(
        render_template(
            template_name,
            data=data,             # the backend JSON, untouched
            error=error,           # None, or a message to show
            role=current_role(),   # for the "logged in as" indicator
            roles=CLINICAL_ROLES,  # for the role-switch dropdown
        )
    )
    return _remember_role(response)


# ============================================================
# Routes - one per template. Each: fetch from backend, pass to template.
# ============================================================
@app.get("/")
def dashboard():
    """
    Landing page. Pulls the caller's clinical records - the backend scopes
    this list to the current role already (assigned doctor / nurse with a
    task / specialist with a consult), so the same route gives each role
    their own view.
    """
    data, error = backend_get("/api/clinical-records/")
    return _render("dashboard.html", data, error)


@app.route("/assessment/new", methods=["GET", "POST"])
def assessment_form():
    """
    Assessment (clinical record) form.

    GET  - render the blank form.
    POST - forward the submitted fields to the backend's create endpoint
           and re-render with whatever it returned (the new record, or an
           error message). No validation here - the backend owns the rules.
    """
    if request.method == "GET":
        return _render("assessment_form.html", data=None, error=None)

    data, error = backend_post("/api/clinical-records/", json_body=request.form.to_dict())
    return _render("assessment_form.html", data, error)


@app.get("/records/admission/<int:admission_id>")
def record_history(admission_id):
    """
    Full clinical history for one admission. Straight passthrough of the
    backend's admission-scoped endpoint - it decides which records this
    role may see and returns an empty list otherwise.
    """
    data, error = backend_get(
        "/api/clinical-records/admission/{}".format(admission_id)
    )
    return _render("record_history.html", data, error)


@app.route("/consultations/new", methods=["GET", "POST"])
def consultation_form():
    """
    Consultation-request form (a doctor raises this against a record).

    GET  - blank form. POST - forward fields to the backend and re-render.
    """
    if request.method == "GET":
        return _render("consultation_form.html", data=None, error=None)

    data, error = backend_post("/api/consultations/", json_body=request.form.to_dict())
    return _render("consultation_form.html", data, error)


@app.get("/consultations/queue")
def consultation_queue():
    """
    Consultation queue. The backend returns the caller's own requests -
    a doctor sees the ones they raised, a specialist the ones addressed to
    them. An optional ?status= is forwarded as-is for filtering.
    """
    params = {}
    if request.args.get("status"):
        params["status"] = request.args["status"]
    data, error = backend_get("/api/consultations/", params=params)
    return _render("consultation_queue.html", data, error)


@app.get("/care-tasks")
def care_tasks():
    """
    Care tasks list. Optional ?nurse_id= / ?clinical_record_id= filters are
    passed straight through to the backend, which owns the filtering.
    """
    params = {}
    for key in ("nurse_id", "clinical_record_id"):
        if request.args.get(key):
            params[key] = request.args[key]
    data, error = backend_get("/api/care-tasks/", params=params)
    return _render("care_tasks.html", data, error)


@app.route("/surgery/new", methods=["GET", "POST"])
def surgery_form():
    """
    Surgery-request form (a doctor schedules a surgery for an admission).

    GET  - blank form. POST - forward fields to the backend and re-render.
    The backend's create does the theatre lookup and Room & Bed dispatch;
    this side just shows the result.
    """
    if request.method == "GET":
        return _render("surgery_form.html", data=None, error=None)

    data, error = backend_post("/api/surgery-requests/", json_body=request.form.to_dict())
    return _render("surgery_form.html", data, error)


@app.route("/patients/<int:admission_id>/summary", methods=["GET", "POST"])
def patient_summary(admission_id):
    """
    Patient summary page for one admission.

    GET  - show the surgery requests raised for this admission.
    POST - additionally ask the backend to generate an AI summary for the
           admission (the backend picks the scope from the current role)
           and pass that straight through too.

    Everything shown is exactly what the backend returned; the role-based
    scope of the AI summary is decided there, not here.
    """
    surgeries, error = backend_get(
        "/api/surgery-requests/admission/{}".format(admission_id)
    )

    summary = None
    if request.method == "POST":
        summary, summary_error = backend_post(
            "/api/ai/summarise-admission", json_body={"admission_id": admission_id}
        )
        # Surface an AI error only if the surgery fetch itself was fine.
        error = error or summary_error

    # Bundle what the backend returned under clear keys for the template.
    data = {
        "admission_id": admission_id,
        "surgery_requests": surgeries,
        "ai_summary": summary,
    }
    return _render("patient_summary.html", data, error)


# ------------------------------------------------------------
# Health check - lets a container platform confirm the UI is up without
# depending on the backend being reachable.
# ------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": "clinical-staff-management-frontend"}


if __name__ == "__main__":
    # Development server. This frontend's assigned port is 3200; the backend
    # it talks to is on 5200.
    # Debug mode (Werkzeug's interactive debugger) is opt-in via FLASK_DEBUG
    # so it can never ship "on" by accident to a reachable deployment - set
    # FLASK_DEBUG=true locally if you want auto-reload and tracebacks.
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=3200, debug=debug_mode)
