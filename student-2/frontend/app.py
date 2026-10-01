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
    redirect,
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

# How long each backend call may take (connect + read), in seconds. Must stay
# comfortably above the backend's own OLLAMA_TIMEOUT (services/ollama_client.py,
# default 30s) so the AI summary request always gets the backend's answer -
# real summary or its own fallback - instead of the frontend giving up first.
BACKEND_TIMEOUT = 40

# Timeouts for the two HTMX fragment routes (/ai/...). Each must stay above the
# backend's own limit for that call (settings.py defaults) so the frontend gives
# up after the backend does and can show the backend's answer:
#   care tasks: backend MCP_TIMEOUT 15s
#   policy:     backend RAG_TIMEOUT 100s, plus up to 10s for the logging write
# The model-backed policy answer is slow, so it gets much more time.
CARE_TASKS_TIMEOUT = 25
POLICY_QUESTION_TIMEOUT = 120

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
def _backend(method, path, timeout=BACKEND_TIMEOUT, **kwargs):
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
            method, url, headers=headers, timeout=timeout, **kwargs
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


def backend_post(path, json_body=None, timeout=BACKEND_TIMEOUT):
    """POST helper - form submissions use this."""
    return _backend("POST", path, timeout=timeout, json=json_body)


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
_DASHBOARD_ENDPOINT_BY_ROLE = {
    "doctor": "/api/clinical-records/",
    "nurse": "/api/care-tasks/",
    "specialist": "/api/consultations/",
}

# care_tasks/ does not scope its own results by caller the way
# clinical-records/ and consultations/ do, so the nurse's dashboard passes
# its own nurse_id explicitly. Mirrors the stand-in ids in auth.py.
_NURSE_STAFF_ID = 7


@app.get("/")
def dashboard():
    """
    Landing page. Pulls the list the active role's dashboard view is built
    from - clinical records for a doctor, care tasks for a nurse,
    consultation requests for a specialist - so dashboard.html's per-role
    stats and table actually have data to render instead of always seeing
    an (empty) clinical-records response.
    """
    role = current_role()
    endpoint = _DASHBOARD_ENDPOINT_BY_ROLE.get(role, "/api/clinical-records/")
    params = {"nurse_id": _NURSE_STAFF_ID} if role == "nurse" else None
    data, error = backend_get(endpoint, params=params)
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


@app.get("/consultations/<int:request_id>/start-review")
def start_review(request_id):
    """
    Specialist's "Start Review" / "Continue Review" dashboard link.

    Simply reads the single request - the backend itself moves a still-
    'requested' row to 'in_review' the first time its own specialist views
    it (routes/consultations.py GET /<id>). This route exists only to trigger
    that read from a dashboard link before sending the specialist on to the
    queue where the row now shows as in_review.
    """
    backend_get("/api/consultations/{}".format(request_id))
    return redirect("/consultations/queue")


@app.get("/care-tasks")
def care_tasks():
    """
    Care tasks list. Scoped to the current nurse by default (mirrors the
    dashboard - care-tasks/ does not filter by caller on its own), same as
    the dashboard's nurse_id stand-in. ?admission_id= narrows that down to
    one admission; ?clinical_record_id= is still honoured for the "view
    tasks for this record" link on the care-task form's success page.
    """
    params = {}
    if current_role() == "nurse":
        params["nurse_id"] = _NURSE_STAFF_ID
    for key in ("clinical_record_id", "admission_id"):
        if request.args.get(key):
            params[key] = request.args[key]
    data, error = backend_get("/api/care-tasks/", params=params)
    return _render("care_tasks.html", data, error)


@app.route("/care-tasks/new", methods=["GET", "POST"])
def care_task_form():
    """
    Care-task form (a doctor raises a task against one of their clinical
    records; the assigned nurse works it from the /care-tasks screen).

    GET  - blank form. POST - forward fields to the backend and re-render.
    The optional due_at is dropped when left blank so the backend does not
    receive an empty string for it; every other field the backend validates.
    """
    if request.method == "GET":
        return _render("care_task_form.html", data=None, error=None)

    submitted = {k: v for k, v in request.form.to_dict().items() if v != ""}
    data, error = backend_post("/api/care-tasks/", json_body=submitted)
    return _render("care_task_form.html", data, error)


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
            "/api/ai/summarise-admission",
            json_body={
                "admission_id": admission_id,
                "prompt": request.form.get("prompt", ""),
            },
        )
        # Surface an AI error only if the surgery fetch itself was fine.
        error = error or summary_error

    # Bundle what the backend returned under clear keys for the template.
    data = {
        "admission_id": admission_id,
        "surgery_requests": surgeries,
        "ai_summary": summary,
        "default_prompt": (
            "Summarise this admission for handover: highlight the current "
            "status, what has been done so far, and what still needs attention."
        ),
    }
    return _render("patient_summary.html", data, error)


# ============================================================
# AI fragments (HTMX)
# Each route returns a small HTML fragment, not a page, for HTMX to swap in.
# The browser only ever talks to this app; this app only ever talks to the
# backend (/api/mcp/call, /api/rag/ask) - never to the MCP or RAG servers.
#
# The fragment is rendered with one `state` variable:
#     ok | disabled | unavailable | insufficient | error
# plus the data for that state. State comes from the backend's `status` word,
# not from guessing at HTTP codes. Every outcome is returned as HTTP 200,
# because HTMX does not swap 4xx/5xx responses by default.
#
# All text shown comes from the backend and is left for Jinja to autoescape;
# nothing here wraps it in Markup or marks it safe.
# ============================================================
_ERROR_UNREACHABLE = "The assistant could not be reached. Please try again."
_ERROR_UNEXPECTED = "The assistant returned an unexpected reply."


def _id_field(raw):
    """
    A form value as a whole number if it looks like one, else unchanged.
    Form fields arrive as strings but the backend only accepts real integers.
    Anything that is not plain digits is passed on as-is so the backend's own
    validation rejects it (the rules live there, not here).
    """
    text = (raw or "").strip()
    if text.isascii() and text.isdigit():
        try:
            return int(text)
        except ValueError:  # absurdly long digit string
            pass
    return text


def _error_details(data):
    """(code, message) from a backend error body; both are None if absent."""
    err = data.get("error") if isinstance(data, dict) else None
    if not isinstance(err, dict):
        return None, None
    code, message = err.get("code"), err.get("message")
    return (
        code if isinstance(code, str) else None,
        message if isinstance(message, str) else None,
    )


def _fragment(template_name, state, **data):
    """Render a fragment with `state` plus its data - no page chrome."""
    return render_template(template_name, state=state, **data)


def _error_fragment(template_name, data=None, unreachable=False):
    """The 'error' state, with the backend's own code/message when it gave one."""
    code, message = _error_details(data)
    if unreachable:
        message = _ERROR_UNREACHABLE
    return _fragment(
        template_name, "error", code=code, message=message or _ERROR_UNEXPECTED
    )


@app.post("/ai/care-tasks")
def ai_care_tasks():
    """
    Open care tasks for one admission, via the backend's MCP route.
    Form field: admission_id.
    """
    data, error = backend_post(
        "/api/mcp/call",
        json_body={
            "tool": "homs_open_care_tasks",
            "arguments": {"admission_id": _id_field(request.form.get("admission_id"))},
        },
        timeout=CARE_TASKS_TIMEOUT,
    )

    template = "partials/mcp_result.html"
    # No usable JSON at all: backend down, timed out, or not speaking JSON.
    if not isinstance(data, dict):
        return _error_fragment(template, unreachable=True)

    status = data.get("status")
    if status == "ok" and not error and "result" in data:
        return _fragment(template, "ok", result=data["result"])
    if status in ("disabled", "unavailable"):
        _, message = _error_details(data)
        return _fragment(template, status, message=message)
    # "error" from the backend, or a reply we do not recognise.
    return _error_fragment(template, data)


@app.post("/ai/policy-question")
def ai_policy_question():
    """
    Policy question answered by the backend's RAG route.
    Form fields: question, admission_id, patient_id (the ids are optional;
    when both are given the backend logs a grounded answer for review).
    """
    body = {"question": request.form.get("question", "")}
    for field in ("admission_id", "patient_id"):
        value = _id_field(request.form.get(field))
        if value != "":  # left blank: omit it, the backend treats it as absent
            body[field] = value

    data, error = backend_post(
        "/api/rag/ask", json_body=body, timeout=POLICY_QUESTION_TIMEOUT
    )

    template = "partials/rag_answer.html"
    if not isinstance(data, dict):
        return _error_fragment(template, unreachable=True)

    status = data.get("status")
    if status in ("answered", "insufficient_context") and not error:
        answer = data.get("answer")
        sources = data.get("sources")
        if not isinstance(answer, str) or not isinstance(sources, list):
            return _error_fragment(template)
        details = {
            "answer": answer,
            "sources": [s for s in sources if isinstance(s, str)],
            "confidence": data.get("confidence"),
            "score": data.get("score"),
            "logged": data.get("logged") is True,
            "summary_id": data.get("summary_id"),
        }
        state = "ok" if status == "answered" else "insufficient"
        return _fragment(template, state, **details)
    if status in ("disabled", "unavailable"):
        _, message = _error_details(data)
        return _fragment(template, status, message=message)
    return _error_fragment(template, data)


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
