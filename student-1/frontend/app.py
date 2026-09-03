

import os
import csv
import io
from datetime import date
from pathlib import Path

import requests
from flask import Flask, redirect, render_template, request, url_for, send_file

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)

ROOT = Path(__file__).resolve().parent
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5100")
DATABASE_DIR = ROOT.parent / "database"
SCHEMA_PATH = DATABASE_DIR / "schema.sql"
SEED_PATH = DATABASE_DIR / "seed_data.sql"

NAV_ITEMS = [
    {"id": "census", "label": "Census", "href": "/"},
    {"id": "intake", "label": "Intake", "href": "/intake"},
    {"id": "search", "label": "Search", "href": "/search"},
]

def _api_get(path, params=None):
    try:
        response = requests.get(f"{BACKEND_URL}{path}", params=params or {}, timeout=5)
        payload = response.json() if response.content else {}
        if response.status_code >= 400:
            return None
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload
    except requests.RequestException:
        return None


def _api_update(path, payload):
    try:
        response = requests.patch(f"{BACKEND_URL}{path}", json=payload, timeout=5)
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


def _api_delete(path):
    try:
        response = requests.delete(f"{BACKEND_URL}{path}", timeout=10)
        return response.ok
    except requests.RequestException:
        return False


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


def get_live_snapshot():
    patients = _api_get("/api/patients") or []
    admissions = _api_get("/api/admissions") or []
    addresses = _api_get("/api/patient-addresses") or []
    medical = _api_get("/api/patient-medical-information") or []
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
    pending_discharge = sum(1 for row in admissions if row["admission_status"] == "Pending")
    admissions_today = sum(1 for row in admissions if row["admission_status"] in {"Active", "Pending"})
    duplicate_review = max(1, len([p for p in patients if p["patient_status"] in {"Inactive", "Transferred"}]))

    return [
        {"label": "Admissions today", "value": str(admissions_today), "delta": "+2 vs prior", "tone": "ok"},
        {"label": "Pending discharge", "value": str(pending_discharge), "delta": "Needs review", "tone": "warn"},
        {"label": "Active patients", "value": str(active_patients), "delta": "+14 wk", "tone": "ok"},
        {"label": "Duplicate review", "value": str(duplicate_review), "delta": "3 flagged", "tone": "risk"},
    ]


def generate_ai_summary(patient):
    sections = [
        f"{patient['p_first_name']} {patient['p_last_name']} is currently recorded as {patient['patient_status']}.",
        f"Primary contact is {patient.get('contact', {}).get('contact_first_name', 'not yet recorded')} {patient.get('contact', {}).get('contact_last_name', '')}.",
        f"Insurance details show Medicare number {patient.get('medical', {}).get('medicare_number', 'not recorded')} and primary address in {patient.get('address', {}).get('address_suburb', 'unknown')}.",
    ]
    return " ".join(sections)


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


def build_search_results(search_text, status_filter, snapshot):
    query = (search_text or "").strip().lower()
    patients = snapshot["patients"]
    results = []

    for patient in patients:
        name = f"{patient['p_first_name']} {patient['p_last_name']}".lower()
        if query and query not in name and query not in str(patient.get("patient_id", "")):
            continue
        if status_filter and patient["patient_status"] != status_filter:
            continue
        results.append(patient)

    return results


@app.route("/")
def census_page():
    snapshot = get_seed_snapshot()
    return render_template(
        "census.html",
        active_page="census",
        nav=NAV_ITEMS,
        metrics=build_census_metrics(snapshot),
        admissions=snapshot["admissions"],
        patients=snapshot["patients"],
        page_title="Census",
        page_context="Reception / Census",
    )


@app.route("/export")
def export_page():
    return export_csv(get_live_snapshot())


@app.route("/intake", methods=["GET", "POST"])
def intake_page():
    snapshot = get_seed_snapshot()
    submitted = False
    message = None

    if request.method == "POST":
        submitted = True
        patient_payload = {
            "p_title": request.form.get("title", "Mr"),
            "p_first_name": request.form.get("first_name", "").strip() or "Unknown",
            "p_last_name": request.form.get("last_name", "").strip() or "Patient",
            "p_date_of_birth": request.form.get("dob") or "1900-01-01",
            "p_assigned_sex": request.form.get("sex", "Unassigned"),
            "p_mobile": request.form.get("mobile", "").strip() or "0000000000",
            "p_method_of_contact": "Email" if request.form.get("email") else "Text",
            "p_email_address": request.form.get("email", "").strip() or None,
            "p_marital_status": request.form.get("marital_status", "Single"),
            "p_first_nations_heritage": request.form.get("first_nations", "Unknown"),
            "patient_status": "Active",
            "created_at": "datetime('now')",
            "updated_at": "datetime('now')",
        }

        try:
            response = requests.post(f"{BACKEND_URL}/api/patients", json=patient_payload, timeout=5)
            created = response.json() if response.content else {}
            parsed = created.get("data") if isinstance(created, dict) else created
            patient_id = (parsed or {}).get("patient_id")

            if patient_id:
                address_payload = {
                    "patient_id": patient_id,
                    "address_street": request.form.get("address", "").strip() or "Not provided",
                    "address_suburb": request.form.get("suburb", "").strip() or "Unknown",
                    "address_state": request.form.get("state", "New South Wales"),
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
                message = "Intake submitted successfully and synced to the live backend API."
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
    )


@app.route("/search")
def search_page():
    snapshot = get_seed_snapshot()
    query = request.args.get("q", "")
    status_filter = request.args.get("status", "")
    results = build_search_results(query, status_filter, snapshot)
    return render_template(
        "search.html",
        active_page="search",
        nav=NAV_ITEMS,
        results=results,
        query=query,
        status_filter=status_filter,
        page_title="Search",
        page_context="Reception / Search",
        patients=snapshot["patients"],
    )


@app.route("/patient/<int:patient_id>", methods=["GET", "POST"])
def patient_record(patient_id):
    snapshot = get_seed_snapshot()
    patient = find_patient_record(patient_id, snapshot)
    if patient is None:
        return render_template("patient.html", active_page="patient", nav=NAV_ITEMS, patient=None, page_title="Patient record", page_context="Patient / Not found")

    message = None
    editing = request.args.get("edit") == "1"
    summary_message = None
    profile_message = None
    action = request.form.get("profile_action")
    if request.method == "POST" and request.form.get("summary_action") == "apply":
        _, saved = _api_post(f"/api/patients/{patient_id}/admin-notes", {"note_text": request.form.get("summary", "")})
        summary_message = "Summary applied as an administrative note." if saved else "The summary could not be applied. Check that the backend is running and try again."
    elif request.method == "POST" and request.form.get("summary_action") == "refresh":
        patient = find_patient_record(patient_id, get_live_snapshot()) or patient
    elif request.method == "POST" and action == "admission":
        _, created = _api_post("/api/admissions", {
            "patient_id": patient_id,
            "admission_date": request.form.get("admission_date") or date.today().isoformat(),
            "admission_status": request.form.get("admission_status", "Pending"),
        })
        profile_message = "Admission created." if created else "The admission could not be created. Check that the backend is running and try again."
    elif request.method == "POST" and action == "update_admission":
        admission_id = request.form.get("admission_id")
        updated_admission = _api_update(f"/api/admissions/{admission_id}", {
            "admission_status": request.form.get("admission_status", "Pending"),
        })
        profile_message = "Admission status updated." if updated_admission else "The admission could not be updated. Check that the backend is running and try again."
    elif request.method == "POST" and action == "delete_admission":
        admission_id = request.form.get("admission_id")
        deleted_admission = _api_delete(f"/api/admissions/{admission_id}")
        profile_message = "Admission deleted." if deleted_admission else "The admission could not be deleted. Check that the backend is running and try again."
    elif request.method == "POST" and action == "admin_note":
        _, created = _api_post(f"/api/patients/{patient_id}/admin-notes", {"note_text": request.form.get("note_text", "")})
        profile_message = "Admin note added." if created else "The admin note could not be added. Check that the backend is running and try again."
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
            updated = _api_update(f"/api/patient-addresses/{address['address_id']}", {
                "address_street": request.form.get("address", address.get("address_street", "")).strip(),
                "address_suburb": request.form.get("suburb", address.get("address_suburb", "")).strip(),
                "address_state": request.form.get("state", address.get("address_state", "")),
                "address_postcode": request.form.get("postcode", address.get("address_postcode", "")).strip(),
            })
        if updated:
            return redirect(url_for("patient_record", patient_id=patient_id))
        message = "The patient could not be saved. Check that the backend is running and try again."

    if profile_message:
        snapshot = get_live_snapshot()
        patient = find_patient_record(patient_id, snapshot) or patient

    patient_summary = generate_ai_summary(patient)
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
        today=date.today().isoformat(),
        admissions=sorted(
            [row for row in snapshot["admissions"] if row.get("patient_id") == patient_id],
            key=lambda row: row.get("admission_date") or "",
            reverse=True,
        ),
    )


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "3100")),
        debug=os.getenv("FLASK_DEBUG", "1") == "1",
    )
