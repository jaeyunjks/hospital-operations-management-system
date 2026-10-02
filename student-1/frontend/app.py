

import os
import csv
import io
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path

import requests
from flask import Flask, make_response, redirect, render_template, request, url_for, send_file

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

ROOT = Path(__file__).resolve().parent
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5100")
RAG_API_TIMEOUT = float(os.getenv("RAG_API_TIMEOUT", "105"))
DATABASE_DIR = ROOT.parent / "database"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"
SEED_PATH = DATABASE_DIR / "seed_data.sql"

NAV_ITEMS = [
    {"id": "census", "label": "Census", "href": "/"},
    {"id": "intake", "label": "Intake", "href": "/intake"},
    {"id": "search", "label": "Search", "href": "/search"},
]

MERGE_FIELD_GROUPS = {
    "patient": [
        ("p_title", "Title"), ("p_first_name", "First name"), ("p_last_name", "Last name"),
        ("p_date_of_birth", "Date of birth"), ("p_assigned_sex", "Sex"), ("p_mobile", "Mobile"),
        ("p_method_of_contact", "Preferred contact method"), ("p_middle_name", "Middle name"),
        ("p_preferred_name", "Preferred name"), ("p_maiden_name", "Maiden name"),
        ("p_previous_last_name", "Previous last name"), ("p_international_visitor", "International visitor"),
        ("p_email_address", "Email"), ("p_landline", "Landline"), ("p_marital_status", "Marital status"),
        ("p_first_nations_heritage", "First Nations heritage"), ("p_language_assistance", "Language assistance"),
    ],
    "address": [
        ("address_street", "Street"), ("address_suburb", "Suburb"),
        ("address_state", "State or territory"), ("address_postcode", "Postcode"),
    ],
    "medical": [
        ("medicare_number", "Medicare number"), ("medicare_individual_reference_number", "Medicare IRN"),
        ("medicare_expiry_date", "Medicare expiry"), ("private_insurance", "Private insurance"),
        ("p_centrelink_number", "Centrelink number"),
    ],
}
MERGE_BOOLEAN_FIELDS = {"p_international_visitor", "p_language_assistance", "private_insurance"}
MERGE_REQUIRED_FIELDS = {
    "p_title", "p_first_name", "p_last_name", "p_date_of_birth", "p_assigned_sex", "p_mobile",
    "p_marital_status", "p_first_nations_heritage", "address_street", "address_suburb",
    "address_state", "address_postcode", "medicare_number", "medicare_individual_reference_number",
    "medicare_expiry_date",
}
MERGE_SELECT_OPTIONS = {
    "p_title": ["Mr", "Mrs", "Ms", "Miss", "Dr", "Master", "Other"],
    "p_assigned_sex": ["Male", "Female", "Alternate", "Unassigned"],
    "p_method_of_contact": ["Post", "Email", "Text"],
    "p_marital_status": ["Single", "Married", "De-Facto", "Widowed", "Separated", "Divorced"],
    "p_first_nations_heritage": ["Unknown", "Aboriginal", "Torres Strait Islander", "Both", "Neither"],
    "p_international_visitor": ["0", "1"],
    "p_language_assistance": ["0", "1"],
    "private_insurance": ["0", "1"],
    "address_state": ["New South Wales", "Victoria", "Queensland", "Western Australia", "South Australia", "Tasmania", "Northern Territory", "Australian Capital Territory"],
}
MERGE_DATE_FIELDS = {"p_date_of_birth", "medicare_expiry_date"}

def _api_get(path, params=None, timeout=5):
    try:
        response = requests.get(f"{BACKEND_URL}{path}", params=params or {}, headers=_identity_headers(), timeout=timeout)
        payload = response.json() if response.content else {}
        if response.status_code >= 400:
            return None
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload
    except requests.RequestException:
        return None


def _identity_headers():
    return {key: value for key, value in {
        "X-HOMS-Role": request.headers.get("X-HOMS-Role"),
        "X-HOMS-User-Id": request.headers.get("X-HOMS-User-Id"),
    }.items() if value}


def current_identity():
    return _api_get("/api/auth/identity") or {"role": "System Admin", "name": "System Administrator"}


def _api_update(path, payload):
    try:
        response = requests.patch(f"{BACKEND_URL}{path}", json=payload, headers=_identity_headers(), timeout=5)
        return response.ok
    except requests.RequestException:
        return False


def _api_post(path, payload):
    try:
        response = requests.post(f"{BACKEND_URL}{path}", json=payload, timeout=10)
        data = response.json() if response.content else {}
        return data, response.ok
    except requests.RequestException:
        return {}, False


def _unwrap_data(payload):
    while isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    return payload


def _api_delete(path):
    try:
        response = requests.delete(f"{BACKEND_URL}{path}", timeout=10)
        return response.ok
    except requests.RequestException:
        return False


def generate_summary(patient, admissions=None):
    source = generate_ai_summary(patient, admissions)
    payload, succeeded = _api_post("/api/ai/summary", {"text": source})
    result = payload.get("data", payload) if isinstance(payload, dict) else {}
    if succeeded and result.get("summary"):
        return result["summary"]
    return source


def _fallback_seed_snapshot():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(SCHEMA_PATH.read_text())
    conn.executescript(SEED_PATH.read_text())

    patients = [dict(row) for row in conn.execute("SELECT * FROM patients ORDER BY patient_id").fetchall()]
    admissions = [dict(row) for row in conn.execute("SELECT * FROM admissions ORDER BY admission_id").fetchall()]

    patient_details = []
    for patient in patients:
        address = conn.execute(
            "SELECT * FROM patient_addresses WHERE patient_id = ? AND address_is_primary = 1 LIMIT 1",
            (patient["patient_id"],),
        ).fetchone()
        medical = conn.execute(
            "SELECT * FROM patient_medical_information WHERE patient_id = ? LIMIT 1",
            (patient["patient_id"],),
        ).fetchone()
        contact = conn.execute(
            "SELECT * FROM patient_contacts WHERE patient_id = ? AND contact_primary = 1 LIMIT 1",
            (patient["patient_id"],),
        ).fetchone()

        patient_details.append({
            **patient,
            "address": dict(address) if address else {},
            "medical": dict(medical) if medical else {},
            "contact": dict(contact) if contact else {},
        })

    return {"patients": patient_details, "admissions": admissions}


def get_live_snapshot(include_inactive=False):
    patient_params = {"includeInactive": "true"} if include_inactive else None
    patients = _api_get("/api/patients", params=patient_params) or []
    admissions = _api_get("/api/admissions") or []
    addresses = _api_get("/api/patients/addresses") or []
    medical = _api_get("/api/patients/medical-information") or []
    contacts = _api_get("/api/patients/contacts") or []
    notes = _api_get("/api/patients/admin-notes") or []

    if not patients and not admissions:
        return _fallback_seed_snapshot()

    patient_by_id = {patient["patient_id"]: patient for patient in patients}
    for row in addresses:
        if row.get("patient_id") in patient_by_id:
            patient_by_id[row["patient_id"]].setdefault("address", {})
            if row.get("address_is_primary") in (1, True):
                patient_by_id[row["patient_id"]]["address"] = row

    for row in medical:
        if row.get("patient_id") in patient_by_id:
            patient_by_id[row["patient_id"]].setdefault("medical", {})
            patient_by_id[row["patient_id"]]["medical"] = row

    for row in contacts:
        if row.get("patient_id") in patient_by_id:
            patient_by_id[row["patient_id"]].setdefault("contacts", []).append(row)
            if row.get("contact_primary") in (1, True):
                patient_by_id[row["patient_id"]]["contact"] = row

    for row in notes:
        if row.get("patient_id") in patient_by_id:
            patient_by_id[row["patient_id"]].setdefault("admin_notes", []).append(row)

    return {"patients": list(patient_by_id.values()), "admissions": admissions}


def get_seed_snapshot():
    return get_live_snapshot()


def as_status_class(value):
    if value in {"Active", "Completed", "Low"}:
        return "ok"
    if value in {"Pending", "Awaiting review", "Medium", "Urgent", "Emergency"}:
        return "warn"
    if value in {"High", "Critical", "Risk", "Pending review"}:
        return "risk"
    return "none"


def build_census_metrics(snapshot):
    patients = snapshot["patients"]
    admissions = snapshot["admissions"]

    active_patients = sum(1 for patient in patients if patient["patient_status"] == "Active")
    today = date.today().isoformat()
    upcoming_admissions = sum(
        1 for row in admissions
        if row["admission_status"] == "Pending"
        and str(row.get("admission_date") or "") > datetime.now().isoformat(timespec="minutes")
    )
    admissions_today = sum(
        1 for row in admissions
        if str(row.get("admission_date") or "")[:10] == today
        and row["admission_status"] in {"Active", "Pending", "Completed"}
    )
    duplicate_review = sum(
        1 for patient in patients
        if patient.get("emergency_override") and patient.get("identity_review_status") == "Pending"
    )

    return [
        {"label": "Admissions today", "value": str(admissions_today), "delta": "Scheduled today", "tone": "ok", "href": f"/?admission_date={today}"},
        {"label": "Upcoming admissions", "value": str(upcoming_admissions), "delta": "Scheduled for later", "tone": "warn", "href": "/?admission_status=Pending"},
        {"label": "Active patients", "value": str(active_patients), "delta": "+14 wk", "tone": "ok", "href": "/search?status=Active"},
        {"label": "Duplicate review", "value": str(duplicate_review), "delta": "Awaiting decision", "tone": "risk", "href": "/duplicate-review"},
    ]


def generate_ai_summary(patient, admissions=None):
    notes = " ".join(note.get("note_text", "") for note in patient.get("admin_notes", []))
    patient_admissions = admissions if admissions is not None else patient.get("admissions", [])
    admission_lines = [
        "Admission on {start}; scheduled end {end}; discharged {discharge}; status {status}.".format(
            start=row.get("admission_date") or "date not set",
            end=row.get("admission_end") or "not set",
            discharge=row.get("discharge_date") or "not recorded",
            status=row.get("admission_status") or "not set",
        )
        for row in patient_admissions
    ]
    return (
        "Patient administration notes: "
        f"{notes or 'No notes recorded.'}\n"
        "Admissions: "
        f"{' '.join(admission_lines) if admission_lines else 'No admissions recorded.'}"
    )


def admission_interval(form):
    try:
        admission_date = date.fromisoformat(form.get("admission_date", ""))
        admission_time = time.fromisoformat(form.get("admission_time", ""))
        duration_minutes = int(form.get("duration_minutes", ""))
    except (TypeError, ValueError):
        raise ValueError("Enter a valid admission date, time, and duration.") from None

    if admission_time.tzinfo is not None or admission_time.minute % 15 or admission_time.second or admission_time.microsecond:
        raise ValueError("Admission times must use 15-minute blocks.")
    if duration_minutes < 15 or duration_minutes % 15:
        raise ValueError("Duration must be at least 15 minutes and use 15-minute blocks.")

    start = datetime.combine(admission_date, admission_time)
    end = start + timedelta(minutes=duration_minutes)
    return start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")


def validate_admission_status(status, start):
    if status not in {"Pending", "Active", "Cancelled", "Completed"}:
        raise ValueError("Select a valid admission status.")
    if status == "Pending" and start <= datetime.now():
        raise ValueError("Pending admissions must be scheduled for a future date and time.")
    if status in {"Active", "Completed"} and start > datetime.now():
        raise ValueError("Active or completed admissions must have started.")


def export_csv(snapshot):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Patient ID", "First name", "Last name", "Date of birth", "Status"])
    for patient in snapshot["patients"]:
        writer.writerow([
            patient["patient_id"], patient["p_first_name"], patient["p_last_name"],
            patient["p_date_of_birth"], patient["patient_status"],
        ])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode("utf-8")), mimetype="text/csv", as_attachment=True, download_name="patient-census.csv")


def find_patient_record(patient_id, snapshot):
    for patient in snapshot["patients"]:
        if patient["patient_id"] == patient_id:
            return patient
    return None


def build_search_results(search_text, status_filter, snapshot, emergency_only=False):
    query = (search_text or "").strip().lower()
    patients = snapshot["patients"]
    results = []

    for patient in patients:
        if patient.get("patient_status") == "Merged":
            continue
        name = f"{patient['p_first_name']} {patient['p_last_name']}".lower()
        if query and query not in name and query not in str(patient.get("patient_id", "")):
            continue
        if status_filter and patient["patient_status"] != status_filter:
            continue
        if emergency_only and not patient.get("emergency_override"):
            continue
        results.append(patient)

    return results


@app.route("/")
def census_page():
    snapshot = get_seed_snapshot()
    admission_status = request.args.get("admission_status")
    admission_date = request.args.get("admission_date")
    admissions = [
        row for row in snapshot["admissions"]
        if (not admission_status or row.get("admission_status") == admission_status)
        and (not admission_date or str(row.get("admission_date") or "")[:10] == admission_date)
    ]
    return render_template(
        "census.html",
        active_page="census",
        nav=NAV_ITEMS,
        metrics=build_census_metrics(snapshot),
        admissions=admissions,
        patients=snapshot["patients"],
        identity=current_identity(),
        page_title="Census",
        page_context="Reception / Census",
    )


@app.get("/partials/ward-occupancy")
def ward_occupancy_partial():
    ward_snapshot = _api_get("/api/mcp/ward-occupancy", timeout=20)
    error = None if ward_snapshot and ward_snapshot.get("ok") else "Ward occupancy is unavailable."
    return render_template(
        "partials/ward_occupancy.html",
        ward_snapshot=ward_snapshot,
        error=error,
        initial=False,
    )


def _valid_rag_guidance_result(result):
    if not isinstance(result, dict) or not isinstance(result.get("answer"), str):
        return False
    if result.get("status") == "insufficient_context":
        return result.get("confidence") == "none" and result.get("citations") == []
    if result.get("status") != "answered" or result.get("confidence") not in {"low", "medium", "high"}:
        return False
    citations = result.get("citations")
    return isinstance(citations, list) and all(
        isinstance(citation, dict)
        and citation.get("id") in {"S1", "S2", "S3", "S4"}
        and isinstance(citation.get("title"), str)
        and isinstance(citation.get("section"), str)
        and isinstance(citation.get("snippet"), str)
        for citation in citations
    )


@app.post("/partials/rag-guidance")
def rag_guidance_partial():
    question = request.form.get("question", "")
    if not question.strip() or len(question) > 500:
        return render_template(
            "partials/rag_guidance_result.html", result=None,
            question=question, error_kind="validation",
        )

    question = question.strip()
    try:
        response = requests.post(
            f"{BACKEND_URL}/api/rag/ask",
            json={"question": question},
            headers=_identity_headers(),
            timeout=RAG_API_TIMEOUT,
        )
    except requests.Timeout:
        error_kind = "timeout"
    except requests.RequestException:
        error_kind = "unavailable"
    else:
        error_kind = {
            400: "validation",
            403: "unauthorized",
            502: "invalid",
            504: "timeout",
        }.get(response.status_code)
        if response.status_code == 503:
            error_kind = "unavailable"
        elif response.status_code != 200 and error_kind is None:
            error_kind = "invalid"

        if error_kind is None:
            try:
                payload = response.json()
            except ValueError:
                error_kind = "invalid"
            else:
                result = _unwrap_data(payload)
                if not _valid_rag_guidance_result(result):
                    error_kind = "invalid"
                else:
                    return render_template(
                        "partials/rag_guidance_result.html", result=result,
                        question=question, error_kind=None,
                    )

    return render_template(
        "partials/rag_guidance_result.html", result=None,
        question=question, error_kind=error_kind,
    )


@app.route("/export")
def export_page():
    return export_csv(get_live_snapshot())


@app.route("/intake", methods=["GET", "POST"])
def intake_page():
    snapshot = get_seed_snapshot()
    submitted = False
    message = None
    errors = {}
    emergency_override = request.form.get("emergency_override") == "1"

    if request.method == "POST":
        submitted = True
        required_fields = {
            "title": "Title", "first_name": "First name", "last_name": "Last name",
            "dob": "Date of birth", "sex": "Sex", "mobile": "Mobile",
            "address": "Address", "suburb": "Suburb", "state": "State",
            "postcode": "Postcode", "medicare_number": "Medicare number",
        }
        if not emergency_override:
            errors = {field: label for field, label in required_fields.items() if not request.form.get(field, "").strip()}
        if errors:
            return render_template(
                "intake.html", active_page="intake", nav=NAV_ITEMS,
                patients=snapshot["patients"], page_title="Intake", page_context="Reception / Intake",
                submitted=submitted, message="These fields are required. Use the emergency override if necessary.",
                errors=errors, form=request.form, identity=current_identity(),
            )
        patient_payload = {
            "p_title": request.form.get("title", "").strip() or "Mr",
            "p_first_name": request.form.get("first_name", "").strip() or "Unknown",
            "p_last_name": request.form.get("last_name", "").strip() or "Patient",
            "p_date_of_birth": request.form.get("dob") or "1900-01-01",
            "p_assigned_sex": request.form.get("sex", "").strip() or "Unassigned",
            "p_mobile": request.form.get("mobile", "").strip() or "0000000000",
            "p_method_of_contact": "Email" if request.form.get("email") else "Text",
            "p_email_address": request.form.get("email", "").strip() or None,
            "p_marital_status": request.form.get("marital_status", "").strip() or "Single",
            "p_first_nations_heritage": request.form.get("first_nations", "").strip() or "Unknown",
            "patient_status": "Active",
            "emergency_override": 1 if emergency_override else 0,
            "identity_review_status": "Pending" if emergency_override else "Not required",
            "created_at": "datetime('now')",
            "updated_at": "datetime('now')",
        }

        try:
            response = requests.post(f"{BACKEND_URL}/api/patients", json=patient_payload, timeout=5)
            created = response.json() if response.content else {}
            parsed = _unwrap_data(created)
            patient_id = (parsed or {}).get("patient_id")

            if patient_id:
                address_payload = {
                    "patient_id": patient_id,
                    "address_street": request.form.get("address", "").strip() or "Not provided",
                    "address_suburb": request.form.get("suburb", "").strip() or "Unknown",
                    "address_state": request.form.get("state", "").strip() or "New South Wales",
                    "address_postcode": request.form.get("postcode", "0000"),
                    "address_is_primary": 1,
                }
                requests.post(f"{BACKEND_URL}/api/patient-addresses", json=address_payload, timeout=5)

                insurance_name = request.form.get("insurance", "Medicare")
                requests.post(
                    f"{BACKEND_URL}/api/patient-medical-information",
                    json={
                        "patient_id": patient_id,
                        "medicare_number": request.form.get("medicare_number", "").strip() or "UNKNOWN",
                        "medicare_individual_reference_number": "1",
                        "medicare_expiry_date": "2030-12-31",
                        "private_insurance": 1 if insurance_name.lower() == "private" else 0,
                        "p_centrelink_number": "",
                    },
                    timeout=5,
                )
                if request.form.get("notes", "").strip():
                    _api_post(f"/api/patients/{patient_id}/admin-notes", {"note_text": request.form["notes"].strip()})
                return redirect(url_for("patient_record", patient_id=patient_id))
            else:
                message = "The patient form was processed, but the backend did not return a patient record."
        except requests.RequestException:
            message = "The live backend is not available right now; the form was queued locally but not synced."

    return render_template(
        "intake.html",
        active_page="intake",
        nav=NAV_ITEMS,
        patients=snapshot["patients"],
        page_title="Intake",
        page_context="Reception / Intake",
        submitted=submitted,
        message=message,
        errors=errors,
        form=request.form,
        identity=current_identity(),
    )


@app.route("/search")
def search_page():
    snapshot = get_seed_snapshot()
    query = request.args.get("q", "")
    identity = current_identity()
    status_filter = request.args.get("status", "Active")
    emergency_only = request.args.get("emergency_override") == "1"
    if status_filter != "Active" and identity.get("role") != "System Admin":
        status_filter = "Active"
    results = build_search_results(query, status_filter, snapshot, emergency_only)
    return render_template(
        "search.html",
        active_page="search",
        nav=NAV_ITEMS,
        results=results,
        query=query,
        status_filter=status_filter,
        emergency_only=emergency_only,
        page_title="Search",
        page_context="Reception / Search",
        patients=snapshot["patients"],
        identity=identity,
    )


def duplicate_match_candidates(patient, patients):
    query_first = (patient.get("p_first_name") or "").strip().casefold()
    query_last = (patient.get("p_last_name") or "").strip().casefold()
    query_dob = patient.get("p_date_of_birth")
    candidates = []
    for candidate in patients:
        if candidate.get("patient_id") == patient.get("patient_id"):
            continue
        score = 0
        if query_first and query_first == (candidate.get("p_first_name") or "").strip().casefold():
            score += 3
        if query_last and query_last == (candidate.get("p_last_name") or "").strip().casefold():
            score += 3
        if query_dob and query_dob == candidate.get("p_date_of_birth"):
            score += 4
        if score:
            candidates.append({"patient": candidate, "score": score})
    return sorted(candidates, key=lambda match: (-match["score"], match["patient"].get("patient_id", 0)))


def _merge_is_default(field, value, patient):
    text = "" if value is None else str(value).strip().casefold()
    if not text or text in {"unknown", "unassigned", "not provided", "1900-01-01", "0000"}:
        return True
    if len(text) > 1 and text.isdigit() and set(text) == {"0"}:
        return True
    if not patient.get("emergency_override"):
        return False
    emergency_defaults = {
        "p_title": {"mr"}, "p_last_name": {"patient"}, "p_assigned_sex": {"unassigned"},
        "p_mobile": {"0000000000"}, "p_marital_status": {"single"},
        "address_street": {"not provided"}, "address_suburb": {"unknown"},
        "address_state": {"new south wales"}, "address_postcode": {"0000"},
        "medicare_number": {"unknown"}, "medicare_individual_reference_number": {"1"},
        "medicare_expiry_date": {"2030-12-31"},
    }
    return text in emergency_defaults.get(field, set())


def build_merge_fields(source, duplicate, submitted=None):
    rows = []
    submitted = submitted or {}
    for group, fields in MERGE_FIELD_GROUPS.items():
        source_group = source if group == "patient" else source.get(group) or {}
        duplicate_group = duplicate if group == "patient" else duplicate.get(group) or {}
        for field, label in fields:
            source_original = source_group.get(field)
            duplicate_original = duplicate_group.get(field)
            source_name = f"source_{group}_{field}"
            duplicate_name = f"duplicate_{group}_{field}"
            source_value = source_original
            duplicate_value = duplicate_original
            source_default = _merge_is_default(field, source_original, source) or _merge_is_default(field, source_value, source)
            duplicate_default = _merge_is_default(field, duplicate_original, duplicate) or _merge_is_default(field, duplicate_value, duplicate)
            source_empty = source_value is None or str(source_value).strip() == ""
            duplicate_empty = duplicate_value is None or str(duplicate_value).strip() == ""
            auto_source = not source_empty and not source_default and duplicate_empty
            values_match = source_value == duplicate_value
            both_blank = source_empty and duplicate_empty
            needs_review = not auto_source and not values_match and not both_blank
            rows.append({
                "group": group,
                "field": field,
                "label": label,
                "source_name": source_name,
                "duplicate_name": duplicate_name,
                "source_value": "" if source_value is None else source_value,
                "duplicate_value": "" if duplicate_value is None else duplicate_value,
                "source_default": source_default,
                "duplicate_default": duplicate_default,
                "needs_review": needs_review,
                "auto_source": auto_source,
                "both_blank": both_blank,
                "choice_name": f"choice_{group}_{field}",
                "custom_name": f"custom_{group}_{field}",
                "custom_value": submitted.get(f"custom_{group}_{field}", ""),
                "custom_options": MERGE_SELECT_OPTIONS.get(field),
                "custom_type": "date" if field in MERGE_DATE_FIELDS else "text",
            })
    return rows


def resolve_merge_fields(rows, form):
    values = {group: {} for group in MERGE_FIELD_GROUPS}
    reviewed_fields = []
    errors = []
    for row in rows:
        left = row["source_value"]
        right = row["duplicate_value"]
        getlist = getattr(form, "getlist", None)
        choices = getlist(row["choice_name"]) if getlist else ([form.get(row["choice_name"])] if form.get(row["choice_name"]) else [])
        choice = choices[0] if len(choices) == 1 else ""
        if row["needs_review"] or row["auto_source"]:
            if choice not in {"source", "duplicate", "custom"}:
                errors.append(f"Choose how to resolve {row['label']}.")
                continue
        if choice == "source":
            resolved = left
        elif choice == "duplicate":
            resolved = right
        elif choice == "custom":
            resolved = form.get(row["custom_name"], "")
        elif row["auto_source"]:
            errors.append(f"Choose how to resolve {row['label']}.")
            continue
        else:
            resolved = left

        if row["needs_review"] or row["auto_source"] and choice != "source" or choice == "custom":
            reviewed_fields.append(f"{row['group']}:{row['field']}")

        if row["field"] in MERGE_BOOLEAN_FIELDS:
            if str(resolved).lower() not in {"0", "1", "false", "true"}:
                errors.append(f"Enter 0 or 1 for {row['label']}.")
                continue
            resolved = int(str(resolved).lower() in {"1", "true"})
        elif choice == "custom" and row["custom_options"] and resolved not in row["custom_options"]:
            errors.append(f"Select an allowed value for {row['label']}.")
            continue
        elif resolved == "" and row["field"] in MERGE_REQUIRED_FIELDS and not row["both_blank"]:
            errors.append(f"Enter a value for {row['label']} before merging.")
            continue

        values[row["group"]][row["field"]] = resolved if resolved != "" else ("" if row["both_blank"] else None)
    return values, reviewed_fields, errors


@app.route("/duplicate-review", methods=["GET", "POST"])
def duplicate_review_page():
    snapshot = get_seed_snapshot()
    identity = current_identity()
    message = "Profiles reconciled successfully." if request.args.get("merged") == "1" else None

    if request.method == "POST":
        patient_id = request.form.get("patient_id", type=int)
        candidate_id = request.form.get("candidate_id", type=int)
        decision = request.form.get("decision")
        patient = find_patient_record(patient_id, snapshot) if patient_id else None
        candidate = find_patient_record(candidate_id, snapshot) if candidate_id else None
        if not patient or not patient.get("emergency_override") or patient.get("identity_review_status") != "Pending":
            message = "This emergency profile is not awaiting review."
        elif decision not in {"Duplicate", "Not duplicate"}:
            message = "Choose whether the selected records are duplicates."
        elif decision == "Duplicate" and (not candidate or candidate_id == patient_id):
            message = "Select the matching patient record before confirming a duplicate."
        elif decision == "Duplicate":
            return redirect(url_for(
                "duplicate_merge_page",
                source_id=candidate_id,
                duplicate_id=patient_id,
            ))
        else:
            saved = _api_update(f"/api/patients/{patient_id}", {
                "identity_review_status": decision,
                "identity_review_candidate_id": candidate_id,
                "identity_reviewed_by": identity.get("name", "Unknown user"),
                "identity_reviewed_at": datetime.now().isoformat(timespec="seconds"),
            })
            message = "Review decision recorded." if saved else "The review decision could not be saved. Check the backend and try again."
            if saved:
                snapshot = get_live_snapshot()

    pending_reviews = [
        patient for patient in snapshot["patients"]
        if patient.get("emergency_override") and patient.get("identity_review_status") == "Pending"
    ]
    reviewed = [
        patient for patient in snapshot["patients"]
        if patient.get("emergency_override") and patient.get("identity_review_status") in {"Duplicate", "Not duplicate"}
    ]
    reviews = [{
        "patient": patient,
        "candidates": duplicate_match_candidates(patient, snapshot["patients"]),
    } for patient in pending_reviews]
    return render_template(
        "duplicate_review.html",
        active_page="census",
        nav=NAV_ITEMS,
        identity=identity,
        page_title="Duplicate Review",
        page_context="Reception / Identity Review",
        reviews=reviews,
        reviewed=reviewed,
        message=message,
    )


@app.route("/duplicate-review/merge", methods=["GET", "POST"])
def duplicate_merge_page():
    snapshot = get_seed_snapshot()
    identity = current_identity()
    source_id = request.values.get("source_id", type=int)
    duplicate_id = request.values.get("duplicate_id", type=int)
    source = find_patient_record(source_id, snapshot) if source_id else None
    duplicate = find_patient_record(duplicate_id, snapshot) if duplicate_id else None
    message = None
    errors = []
    pair_valid = bool(source and duplicate and source_id != duplicate_id)
    if source_id or duplicate_id:
        if not source or not duplicate or source_id == duplicate_id:
            pair_valid = False
            errors.append("Choose two different valid patient profiles from Duplicate Review.")
    if source and duplicate:
        potential_ids = {
            match["patient"]["patient_id"]
            for match in duplicate_match_candidates(duplicate, snapshot["patients"])
        }
        if not duplicate.get("emergency_override") or duplicate.get("identity_review_status") != "Pending":
            pair_valid = False
            errors.append("The Duplicate profile is not awaiting emergency identity review.")
        elif source_id not in potential_ids:
            pair_valid = False
            errors.append("Select the Duplicate profile from the potential matches on Duplicate Review.")
    rows = build_merge_fields(source, duplicate, request.form if request.method == "POST" else None) if source and duplicate else []

    if request.method == "POST":
        values, reviewed_fields, field_errors = resolve_merge_fields(rows, request.form) if rows else ({}, [], [])
        errors.extend(field_errors)
        if not errors:
            _, succeeded = _api_post("/api/patients/merge", {
                "source_id": source_id,
                "duplicate_id": duplicate_id,
                "source": values,
                "reviewed_fields": reviewed_fields,
                "reviewed_by": identity.get("name", "Unknown user"),
                "contact_ids": request.form.getlist("contact_ids", type=int),
                "admission_ids": request.form.getlist("admission_ids", type=int),
            })
            if succeeded:
                return redirect(url_for("duplicate_review_page", merged="1"))
            message = "The profiles could not be merged. No partial changes were saved; review the selections and try again."

    field_groups = [
        (title, [row for row in rows if row["group"] == group])
        for group, title in (("patient", "Identity and demographics"), ("address", "Primary address"), ("medical", "Insurance and identifiers"))
    ]
    return render_template(
        "duplicate_merge.html",
        active_page="census",
        nav=NAV_ITEMS,
        identity=identity,
        page_title="Reconcile Patient Profiles",
        page_context="Reception / Duplicate Review / Reconciliation",
        patients=snapshot["patients"],
        source=source,
        duplicate=duplicate,
        pair_valid=pair_valid,
        source_id=source_id,
        duplicate_id=duplicate_id,
        field_groups=field_groups,
        source_admissions=[row for row in snapshot["admissions"] if source and row.get("patient_id") == source_id],
        duplicate_admissions=[row for row in snapshot["admissions"] if duplicate and row.get("patient_id") == duplicate_id],
        source_contacts=(source or {}).get("contacts", []),
        duplicate_contacts=(duplicate or {}).get("contacts", []),
        errors=errors,
        message=message,
        submitted=request.form if request.method == "POST" else {},
    )


@app.route("/patient/<int:patient_id>", methods=["GET", "POST"])
def patient_record(patient_id):
    snapshot = get_seed_snapshot()
    patient = find_patient_record(patient_id, snapshot)
    if patient is None:
        snapshot = get_live_snapshot(include_inactive=True)
        patient = find_patient_record(patient_id, snapshot)
    if patient is None:
        return render_template("patient.html", active_page="patient", nav=NAV_ITEMS, patient=None, page_title="Patient record", page_context="Patient / Not found", identity=current_identity())

    message = None
    editing = request.args.get("edit") == "1"
    summary_message = None
    profile_message = None
    summary_override = None
    admissions = [row for row in snapshot["admissions"] if row.get("patient_id") == patient_id]
    action = request.form.get("profile_action")
    if request.method == "POST" and request.form.get("summary_action") == "apply":
        summary_text = request.form.get("summary", "").strip() or generate_summary(patient)
        _, saved = _api_post(f"/api/patients/{patient_id}/admin-notes", {"note_text": summary_text})
        summary_message = "Summary applied as an administrative note." if saved else "The summary could not be applied. Check that the backend is running and try again."
    elif request.method == "POST" and request.form.get("summary_action") == "refresh":
        live_snapshot = get_live_snapshot(include_inactive=True)
        patient = find_patient_record(patient_id, live_snapshot) or patient
        admissions = [row for row in live_snapshot["admissions"] if row.get("patient_id") == patient_id]
        summary_override = generate_summary(patient, admissions)
        if request.headers.get("HX-Request") == "true":
            return make_response(escape(summary_override), 200, {"Content-Type": "text/html; charset=utf-8"})
    elif request.method == "POST" and action == "admission":
        try:
            admission_start, admission_end = admission_interval(request.form)
            status = request.form.get("admission_status", "Pending")
            validate_admission_status(status, datetime.fromisoformat(admission_start))
            _, created = _api_post("/api/admissions", {
                "patient_id": patient_id,
                "admission_date": admission_start,
                "admission_end": admission_end,
                "admission_status": status,
            })
            profile_message = "Admission created." if created else "The admission could not be created. Check that the backend is running and try again."
        except ValueError as exc:
            profile_message = str(exc)
    elif request.method == "POST" and action == "update_admission":
        admission_id = request.form.get("admission_id")
        status = request.form.get("admission_status", "Pending")
        admission = next((row for row in admissions if str(row.get("admission_id")) == admission_id), None)
        try:
            validate_admission_status(status, datetime.fromisoformat(admission["admission_date"]))
            updated_admission = _api_update(f"/api/admissions/{admission_id}", {"admission_status": status})
            profile_message = "Admission status updated." if updated_admission else "The admission could not be updated. Check that the backend is running and try again."
        except (ValueError, TypeError, KeyError):
            profile_message = "The selected status does not match the scheduled admission time."
    elif request.method == "POST" and action == "delete_admission":
        admission_id = request.form.get("admission_id")
        deleted_admission = _api_delete(f"/api/admissions/{admission_id}")
        profile_message = "Admission deleted." if deleted_admission else "The admission could not be deleted. Check that the backend is running and try again."
    elif request.method == "POST" and action == "admin_note":
        _, created = _api_post(f"/api/patients/{patient_id}/admin-notes", {"note_text": request.form.get("note_text", "")})
        profile_message = "Admin note added." if created else "The admin note could not be added. Check that the backend is running and try again."
    elif request.method == "POST" and action == "medical":
        medical = patient.get("medical") or {}
        medical_payload = {
            "patient_id": patient_id,
            "medicare_number": request.form.get("medicare_number", "").strip(),
            "medicare_individual_reference_number": request.form.get("medicare_individual_reference_number", "").strip(),
            "medicare_expiry_date": request.form.get("medicare_expiry_date", ""),
            "private_insurance": 1 if request.form.get("private_insurance") == "1" else 0,
            "p_centrelink_number": request.form.get("p_centrelink_number", "").strip(),
        }
        if medical.get("insurance_id"):
            saved = _api_update(
                f"/api/patients/medical-information/{medical['insurance_id']}",
                {key: value for key, value in medical_payload.items() if key != "patient_id"},
            )
        else:
            _, saved = _api_post("/api/patients/medical-information", medical_payload)
        profile_message = "Medical information updated." if saved else "Medical information could not be saved. Check the backend and try again."
    elif request.method == "POST" and action == "contact":
        _, created = _api_post("/api/patients/contacts", {
            "patient_id": patient_id,
            "contact_primary": 1 if request.form.get("contact_primary") else 0,
            "contact_first_name": request.form.get("contact_first_name", "").strip(),
            "contact_last_name": request.form.get("contact_last_name", "").strip(),
            "contact_date_of_birth": request.form.get("contact_date_of_birth") or "1900-01-01",
            "contact_relationship": request.form.get("contact_relationship", "").strip(),
            "contact_address_same_as_patient": 1 if request.form.get("contact_address_same_as_patient") else 0,
            "contact_address": request.form.get("contact_address", "").strip() or "Not provided",
            "contact_mobile": request.form.get("contact_mobile", "").strip(),
            "contact_landline": request.form.get("contact_landline", "").strip(),
            "contact_email": request.form.get("contact_email", "").strip(),
        })
        profile_message = "Patient contact added." if created else "The patient contact could not be added. Check that the backend is running and try again."
    elif request.method == "POST" and action == "edit_contact":
        contact_id = request.form.get("contact_id")
        updated_contact = _api_update(f"/api/patients/contacts/{contact_id}", {
            "contact_first_name": request.form.get("contact_first_name", "").strip(),
            "contact_last_name": request.form.get("contact_last_name", "").strip(),
            "contact_date_of_birth": request.form.get("contact_date_of_birth") or "1900-01-01",
            "contact_relationship": request.form.get("contact_relationship", "").strip(),
            "contact_address": request.form.get("contact_address", "").strip() or "Not provided",
            "contact_mobile": request.form.get("contact_mobile", "").strip(),
            "contact_landline": request.form.get("contact_landline", "").strip(),
            "contact_email": request.form.get("contact_email", "").strip(),
        })
        profile_message = "Patient contact updated." if updated_contact else "The patient contact could not be updated. Check that the backend is running and try again."
    elif request.method == "POST" and action == "delete_contact":
        contact_id = request.form.get("contact_id")
        deleted_contact = _api_delete(f"/api/patients/contacts/{contact_id}")
        profile_message = "Patient contact deleted." if deleted_contact else "The patient contact could not be deleted. Check that the backend is running and try again."
    elif request.method == "POST":
        updated = _api_update(f"/api/patients/{patient_id}", {
            "p_title": request.form.get("title", patient["p_title"]),
            "p_first_name": request.form.get("first_name", patient["p_first_name"]).strip(),
            "p_last_name": request.form.get("last_name", patient["p_last_name"]).strip(),
            "p_date_of_birth": request.form.get("dob", patient["p_date_of_birth"]),
            "p_assigned_sex": request.form.get("sex", patient["p_assigned_sex"]),
            "p_mobile": request.form.get("mobile", patient["p_mobile"]).strip(),
            "p_email_address": request.form.get("email", "").strip() or None,
            "p_marital_status": patient["p_marital_status"],
            "p_first_nations_heritage": patient["p_first_nations_heritage"],
            "patient_status": request.form.get("patient_status", patient["patient_status"]),
        })
        address = patient.get("address", {})
        if updated and address:
            updated = _api_update(f"/api/patients/addresses/{address['address_id']}", {
                "address_street": request.form.get("address", address.get("address_street", "")).strip(),
                "address_suburb": request.form.get("suburb", address.get("address_suburb", "")).strip(),
                "address_state": request.form.get("state", address.get("address_state", "")),
                "address_postcode": request.form.get("postcode", address.get("address_postcode", "")).strip(),
            })
        if updated:
            return redirect(url_for("patient_record", patient_id=patient_id))
        message = "The patient could not be saved. Check that the backend is running and try again."

    if profile_message:
        snapshot = get_live_snapshot(include_inactive=True)
        patient = find_patient_record(patient_id, snapshot) or patient
        admissions = [row for row in snapshot["admissions"] if row.get("patient_id") == patient_id]

    patient_summary = summary_override or generate_summary(patient, admissions)
    return render_template(
        "patient.html",
        active_page="patient",
        nav=NAV_ITEMS,
        patient=patient,
        ai_summary=patient_summary,
        page_title="Patient record",
        page_context="Patient / Record",
        editing=editing,
        message=message,
        summary_message=summary_message,
        profile_message=profile_message,
        today=(date.today() + timedelta(days=1)).isoformat(),
        admissions=sorted(admissions, key=lambda row: row.get("admission_date") or "", reverse=True),
        identity=current_identity(),
    )


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "3100")),
        debug=os.getenv("FLASK_DEBUG", "1") == "1",
    )
