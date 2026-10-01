from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from frontend import app as frontend_app


@pytest.fixture
def client(monkeypatch):
    snapshot = {
        "patients": [
            {
                "patient_id": 1,
                "p_title": "Mr",
                "p_first_name": "Alex",
                "p_last_name": "Patient",
                "p_date_of_birth": "2000-01-01",
                "p_assigned_sex": "Male",
                "p_mobile": "0400000000",
                "p_email_address": None,
                "p_marital_status": "Single",
                "p_first_nations_heritage": "Unknown",
                "patient_status": "Active",
                "address": {},
                "medical": {},
                "contacts": [],
                "admin_notes": [],
            }
        ],
        "admissions": [],
    }
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        frontend_app,
        "current_identity",
        lambda: {"role": "Receptionist", "name": "Receptionist"},
    )
    frontend_app.app.config.update(TESTING=True)
    return frontend_app.app.test_client()


def api_response(payload, status_code=200):
    response = Mock()
    response.content = b"{}"
    response.status_code = status_code
    response.ok = status_code < 400
    response.json.return_value = payload
    return response


def merge_snapshot():
    profile = {
        "p_title": "Ms", "p_first_name": "Alex", "p_last_name": "Patient",
        "p_date_of_birth": "1980-01-01", "p_assigned_sex": "Female", "p_mobile": "0412345678",
        "p_method_of_contact": "Text", "p_middle_name": "Lee", "p_preferred_name": "Alex",
        "p_maiden_name": "", "p_previous_last_name": "", "p_international_visitor": 0,
        "p_email_address": "alex@example.com", "p_landline": "0299999999", "p_marital_status": "Single",
        "p_first_nations_heritage": "Neither", "p_language_assistance": 0,
    }
    address = {
        "address_street": "1 Main Road", "address_suburb": "Sydney",
        "address_state": "New South Wales", "address_postcode": "2000",
    }
    medical = {
        "medicare_number": "1234567890", "medicare_individual_reference_number": "1",
        "medicare_expiry_date": "2030-12-31", "private_insurance": 0, "p_centrelink_number": "CL-123",
    }
    source = {
        **profile, "patient_id": 1, "patient_status": "Active", "emergency_override": 0,
        "identity_review_status": "Not required", "address": {**address}, "medical": {**medical},
        "contacts": [], "admin_notes": [],
    }
    duplicate = {
        **profile, "patient_id": 2, "patient_status": "Active", "emergency_override": 1,
        "identity_review_status": "Pending", "p_date_of_birth": "1981-01-01", "p_mobile": "0000000000",
        "address": {**address}, "medical": {**medical, "medicare_number": "0987654321"},
        "contacts": [{
            "contact_id": 50, "contact_first_name": "Taylor", "contact_last_name": "Contact",
            "contact_date_of_birth": "1970-01-01", "contact_relationship": "Sibling",
            "contact_mobile": "0400000000", "contact_email": "", "contact_address": "1 Main Road",
        }],
        "admin_notes": [],
    }
    return {
        "patients": [source, duplicate],
        "admissions": [{
            "admission_id": 90, "patient_id": 2, "admission_date": "2030-01-01T09:00",
            "admission_end": "2030-01-01T10:00", "admission_status": "Pending",
        }],
    }


def merge_form_data(snapshot, resolutions=True):
    source = next(patient for patient in snapshot["patients"] if patient["patient_id"] == 1)
    duplicate = next(patient for patient in snapshot["patients"] if patient["patient_id"] == 2)
    data = {"source_id": "1", "duplicate_id": "2", "contact_ids": "50", "admission_ids": "90"}
    for row in frontend_app.build_merge_fields(source, duplicate):
        if resolutions and row["needs_review"]:
            data[row["choice_name"]] = "duplicate"
        elif row["auto_source"]:
            data[row["choice_name"]] = "source"
    return data


def test_empty_intake_shows_required_fields(client):
    response = client.post("/intake", data={})

    assert response.status_code == 200
    assert b"These fields are required" in response.data
    assert response.data.count(b'class="required-star"') >= 8


def test_census_has_collapsed_lazy_load_ward_occupancy_tile(client, monkeypatch):
    api_get = Mock()
    monkeypatch.setattr(frontend_app, "_api_get", api_get)

    response = client.get("/")

    assert response.status_code == 200
    assert b'<details class="panel ward-occupancy-panel">' in response.data
    assert b"<summary" in response.data
    assert b' hx-get="/partials/ward-occupancy"' in response.data
    assert b"Load occupancy" in response.data
    assert b"Emergency identity review" not in response.data
    api_get.assert_not_called()


def test_ward_occupancy_partial_shows_loaded_snapshot(client, monkeypatch):
    snapshot = {
        "ok": True,
        "data": {
            "totals": {"total_beds": 8, "occupied": 5, "available": 2,
                       "occupancy_pct": 62.5},
            "wards": [{"ward": "Emergency", "total_beds": 8, "occupied": 5,
                       "available": 2, "reserved": 1, "maintenance": 0,
                       "occupancy_pct": 62.5}],
        },
    }
    api_get = Mock(return_value=snapshot)
    monkeypatch.setattr(frontend_app, "_api_get", api_get)

    response = client.get("/partials/ward-occupancy")

    assert response.status_code == 200
    assert b"Emergency" in response.data
    assert b"62.5%" in response.data
    assert b"does not reserve or assign a bed" in response.data
    api_get.assert_called_once_with("/api/mcp/ward-occupancy", timeout=20)


def test_ward_occupancy_partial_handles_unavailable_snapshot(client, monkeypatch):
    monkeypatch.setattr(frontend_app, "_api_get", Mock(return_value=None))

    response = client.get("/partials/ward-occupancy")

    assert response.status_code == 200
    assert b"Ward occupancy is unavailable" in response.data


def test_emergency_intake_redirects_for_nested_patient_response(client, monkeypatch):
    monkeypatch.setattr(
        frontend_app.requests,
        "post",
        Mock(
            side_effect=[
                api_response({"success": True, "data": {"data": {"patient_id": 42}}}, 201),
                api_response({}),
                api_response({}),
            ]
        ),
    )

    response = client.post("/intake", data={"emergency_override": "1"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/patient/42")
    assert frontend_app.requests.post.call_args_list[0].kwargs["json"]["emergency_override"] == 1
    assert frontend_app.requests.post.call_args_list[0].kwargs["json"]["identity_review_status"] == "Pending"


def test_search_can_filter_to_emergency_override_profiles(client):
    snapshot = frontend_app.get_seed_snapshot()
    snapshot["patients"][0]["emergency_override"] = 1
    snapshot["patients"].append({
        **snapshot["patients"][0],
        "patient_id": 2,
        "p_first_name": "Jordan",
        "emergency_override": 0,
    })
    response = client.get("/search?emergency_override=1")

    assert response.status_code == 200
    assert b"Emergency override profiles" in response.data
    assert b"MRN-1" in response.data
    assert b"MRN-2" not in response.data


def test_duplicate_review_requires_and_records_staff_decision(client, monkeypatch):
    snapshot = frontend_app.get_seed_snapshot()
    emergency_patient = {
        **snapshot["patients"][0],
        "patient_id": 2,
        "emergency_override": 1,
        "identity_review_status": "Pending",
    }
    snapshot["patients"].append(emergency_patient)
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)
    monkeypatch.setattr(frontend_app, "get_live_snapshot", lambda: snapshot)
    save_review = Mock(return_value=True)
    monkeypatch.setattr(frontend_app, "_api_update", save_review)

    page = client.get("/duplicate-review")
    assert page.status_code == 200
    assert b"Confirm duplicate" in page.data
    assert b"Not a duplicate" in page.data
    save_review.assert_not_called()

    response = client.post("/duplicate-review", data={
        "patient_id": "2",
        "candidate_id": "1",
        "decision": "Duplicate",
    })

    assert response.status_code == 302
    assert "/duplicate-review/merge?source_id=1&duplicate_id=2" in response.headers["Location"]
    save_review.assert_not_called()


def test_duplicate_review_does_not_accept_duplicate_without_candidate(client, monkeypatch):
    snapshot = frontend_app.get_seed_snapshot()
    snapshot["patients"][0]["emergency_override"] = 1
    snapshot["patients"][0]["identity_review_status"] = "Pending"
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)
    save_review = Mock(return_value=True)
    monkeypatch.setattr(frontend_app, "_api_update", save_review)

    response = client.post("/duplicate-review", data={
        "patient_id": "1",
        "decision": "Duplicate",
    })

    assert response.status_code == 200
    assert b"Select the matching patient record" in response.data
    save_review.assert_not_called()


def test_merge_comparison_marks_conflicts_and_default_values(client, monkeypatch):
    snapshot = merge_snapshot()
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)

    response = client.get("/duplicate-review/merge?source_id=1&duplicate_id=2")

    assert response.status_code == 200
    assert b"Source \xc2\xb7 retained profile" in response.data
    assert b"Duplicate \xc2\xb7 merged into Source" in response.data
    assert b"merge-conflict" in response.data
    assert "†".encode() in response.data
    assert b'<select name="source_id"' not in response.data
    assert b'<select name="duplicate_id"' not in response.data
    assert b'readonly aria-readonly="true"' in response.data
    assert b'target="_blank"' in response.data
    assert b"updateMergeField(this)" in response.data
    assert b'<select id="custom_patient_p_title"' in response.data
    assert b'<select id="custom_patient_p_assigned_sex"' in response.data
    assert b'value="source" aria-label="Use Source Middle name" checked' in response.data
    assert b"name=\"contact_ids\" value=\"50\" checked" in response.data
    assert b"name=\"admission_ids\" value=\"90\" checked" in response.data


def test_merge_cannot_submit_until_each_conflict_is_assessed(client, monkeypatch):
    snapshot = merge_snapshot()
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)
    merge_api = Mock(return_value=({}, True))
    monkeypatch.setattr(frontend_app, "_api_post", merge_api)
    form = merge_form_data(snapshot)
    form["source_patient_p_date_of_birth"] = "1901-01-01"
    form.pop("choice_patient_p_date_of_birth")
    form.pop("choice_medical_medicare_number")

    response = client.post("/duplicate-review/merge", data=form)

    assert response.status_code == 200
    assert b"Choose how to resolve Date of birth" in response.data
    assert b"Choose how to resolve Medicare number" in response.data
    merge_api.assert_not_called()


def test_merge_submits_resolved_profile_and_selected_related_records(client, monkeypatch):
    snapshot = merge_snapshot()
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)
    merge_api = Mock(return_value=({}, True))
    monkeypatch.setattr(frontend_app, "_api_post", merge_api)
    form = merge_form_data(snapshot)
    rows = frontend_app.build_merge_fields(snapshot["patients"][0], snapshot["patients"][1], form)
    _values, _reviewed, field_errors = frontend_app.resolve_merge_fields(rows, form)
    assert not field_errors, field_errors

    response = client.post("/duplicate-review/merge", data=form)

    assert response.status_code == 302, response.data.decode()
    assert response.headers["Location"].endswith("/duplicate-review?merged=1")
    path, payload = merge_api.call_args.args
    assert path == "/api/patients/merge"
    assert payload["source"]["patient"]["p_date_of_birth"] == "1981-01-01"
    assert payload["source"]["medical"]["medicare_number"] == "0987654321"
    assert payload["contact_ids"] == [50]
    assert payload["admission_ids"] == [90]
    assert "patient:p_date_of_birth" in payload["reviewed_fields"]
    assert payload["reviewed_by"] == "Receptionist"


def test_merge_page_rejects_source_not_in_potential_matches(client, monkeypatch):
    snapshot = merge_snapshot()
    unrelated = {**snapshot["patients"][0], "patient_id": 3, "p_first_name": "Different", "p_last_name": "Person"}
    snapshot["patients"].append(unrelated)
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)

    response = client.get("/duplicate-review/merge?source_id=3&duplicate_id=2")

    assert response.status_code == 200
    assert b"Select the Duplicate profile from the potential matches" in response.data
    assert b"Reconcile and merge profiles" not in response.data


def test_merge_default_selection_and_empty_side_rules():
    snapshot = merge_snapshot()
    source, duplicate = snapshot["patients"]
    source["p_middle_name"] = "Lee"
    duplicate["p_middle_name"] = ""
    source["p_preferred_name"] = ""
    duplicate["p_preferred_name"] = "Alex"

    rows = frontend_app.build_merge_fields(source, duplicate)
    by_field = {row["field"]: row for row in rows}

    assert by_field["p_middle_name"]["auto_source"] is True
    assert by_field["p_middle_name"]["needs_review"] is False
    assert by_field["p_preferred_name"]["auto_source"] is False
    assert by_field["p_preferred_name"]["needs_review"] is True

    source["p_title"] = "Unknown"
    duplicate["p_title"] = ""
    rows = frontend_app.build_merge_fields(source, duplicate)
    title = next(row for row in rows if row["field"] == "p_title")
    assert title["auto_source"] is False
    assert title["needs_review"] is True

    source["p_maiden_name"] = ""
    duplicate["p_maiden_name"] = ""
    rows = frontend_app.build_merge_fields(source, duplicate)
    blank = next(row for row in rows if row["field"] == "p_maiden_name")
    assert blank["both_blank"] is True
    assert blank["needs_review"] is False


def test_search_never_returns_merged_patients_or_offers_merged_filter(client, monkeypatch):
    snapshot = frontend_app.get_seed_snapshot()
    merged_patient = {
        **snapshot["patients"][0],
        "patient_id": 7,
        "p_first_name": "Merged",
        "patient_status": "Merged",
    }
    snapshot["patients"].append(merged_patient)
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)
    monkeypatch.setattr(frontend_app, "current_identity", lambda: {"role": "System Admin", "name": "Admin"})

    all_statuses = client.get("/search?status=")
    explicit_merged = client.get("/search?status=Merged")

    assert all_statuses.status_code == 200
    assert b"MRN-7" not in all_statuses.data
    assert b'<option value="Merged"' not in all_statuses.data
    assert b"Merged Patient" not in explicit_merged.data


def test_custom_predefined_fields_only_accept_list_values():
    snapshot = merge_snapshot()
    form = merge_form_data(snapshot)
    form["choice_patient_p_title"] = "custom"
    form["custom_patient_p_title"] = "Dr"
    rows = frontend_app.build_merge_fields(snapshot["patients"][0], snapshot["patients"][1])

    values, _reviewed, errors = frontend_app.resolve_merge_fields(rows, form)

    assert not errors
    assert values["patient"]["p_title"] == "Dr"

    form["custom_patient_p_title"] = "Unlisted title"
    _values, _reviewed, errors = frontend_app.resolve_merge_fields(rows, form)

    assert "Select an allowed value for Title." in errors


def test_summary_uses_patient_notes():
    patient = {"admin_notes": [{"note_text": "Patient requires interpreter support."}]}

    summary = frontend_app.generate_ai_summary(patient)

    assert "Patient requires interpreter support." in summary
    assert "Admissions: No admissions recorded." in summary


def test_summary_includes_patient_admissions():
    patient = {"admin_notes": [{"note_text": "Needs interpreter support."}]}
    admissions = [{
        "admission_date": "2030-01-02T13:00",
        "admission_end": "2030-01-02T13:15",
        "admission_status": "Pending",
    }]

    summary = frontend_app.generate_ai_summary(patient, admissions)

    assert "Needs interpreter support." in summary
    assert "2030-01-02T13:00" in summary
    assert "2030-01-02T13:15" in summary
    assert "Pending" in summary


def test_admission_interval_enforces_fifteen_minute_blocks():
    start, end = frontend_app.admission_interval({
        "admission_date": "2030-01-02",
        "admission_time": "13:00",
        "duration_minutes": "15",
    })

    assert start == "2030-01-02T13:00"
    assert end == "2030-01-02T13:15"
    with pytest.raises(ValueError, match="15-minute blocks"):
        frontend_app.admission_interval({
            "admission_date": "2030-01-02",
            "admission_time": "13:00",
            "duration_minutes": "10",
        })


def test_create_admission_sends_start_and_scheduled_end(client, monkeypatch):
    create_admission = Mock(return_value=({}, True))
    monkeypatch.setattr(frontend_app, "_api_post", create_admission)
    scheduled_date = (date.today() + timedelta(days=7)).isoformat()
    refreshed_snapshot = frontend_app.get_seed_snapshot()
    refreshed_snapshot["admissions"] = [{
        "patient_id": 1,
        "admission_date": f"{scheduled_date}T13:00",
        "admission_end": f"{scheduled_date}T13:15",
        "admission_status": "Pending",
    }]
    monkeypatch.setattr(frontend_app, "get_live_snapshot", lambda include_inactive=False: refreshed_snapshot)
    monkeypatch.setattr(frontend_app, "generate_summary", lambda _patient, _admissions: "Patient summary")

    response = client.post("/patient/1", data={
        "profile_action": "admission",
        "admission_date": scheduled_date,
        "admission_time": "13:00",
        "duration_minutes": "15",
        "admission_status": "Pending",
    })

    assert response.status_code == 200
    assert create_admission.call_args.args[0] == "/api/admissions"
    assert create_admission.call_args.args[1]["admission_date"] == f"{scheduled_date}T13:00"
    assert create_admission.call_args.args[1]["admission_end"] == f"{scheduled_date}T13:15"
    assert f"{scheduled_date}T13:15".encode() in response.data


def test_create_admission_rejects_non_block_duration(client, monkeypatch):
    create_admission = Mock()
    monkeypatch.setattr(frontend_app, "_api_post", create_admission)
    monkeypatch.setattr(frontend_app, "generate_summary", lambda _patient, _admissions: "Patient summary")

    response = client.post("/patient/1", data={
        "profile_action": "admission",
        "admission_date": "2030-01-02",
        "admission_time": "13:00",
        "duration_minutes": "10",
        "admission_status": "Pending",
    })

    assert response.status_code == 200
    assert b"Duration must be at least 15 minutes" in response.data
    create_admission.assert_not_called()


def test_census_metrics_count_only_future_pending_admissions():
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    metrics = frontend_app.build_census_metrics({
        "patients": [],
        "admissions": [
            {"admission_status": "Pending", "admission_date": f"{tomorrow}T09:00"},
            {"admission_status": "Pending", "admission_date": f"{yesterday}T09:00"},
            {"admission_status": "Active", "admission_date": f"{today}T09:00"},
            {"admission_status": "Completed", "admission_date": f"{today}T08:00"},
            {"admission_status": "Cancelled", "admission_date": f"{today}T10:00"},
        ],
    })

    assert metrics[0]["value"] == "2"
    assert metrics[1]["label"] == "Upcoming admissions"
    assert metrics[1]["value"] == "1"


def test_htmx_summary_refresh_returns_plain_text_fragment(client, monkeypatch):
    monkeypatch.setattr(frontend_app, "get_live_snapshot", lambda include_inactive=False: frontend_app.get_seed_snapshot())
    monkeypatch.setattr(frontend_app, "generate_summary", lambda _patient, _admissions: "Updated <summary>")

    response = client.post(
        "/patient/1",
        data={"summary_action": "refresh"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.data == b"Updated &lt;summary&gt;"


def test_merged_patient_is_available_by_direct_profile_url_and_summary_is_immediate(client, monkeypatch):
    patient = {
        **merge_snapshot()["patients"][1],
        "patient_id": 11,
        "patient_status": "Merged",
        "deactivated_at": "2026-10-01T10:00:00",
    }
    archived_snapshot = {"patients": [patient], "admissions": []}
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: {"patients": [], "admissions": []})
    inactive_lookup = Mock(return_value=archived_snapshot)
    monkeypatch.setattr(frontend_app, "get_live_snapshot", inactive_lookup)
    monkeypatch.setattr(frontend_app, "generate_summary", lambda _patient, _admissions: "AI summary ready on opening")

    response = client.get("/patient/11")

    assert response.status_code == 200
    assert b"MRN-11" in response.data
    assert b"Merged" in response.data
    assert b"AI summary ready on opening" in response.data
    inactive_lookup.assert_called_once_with(include_inactive=True)


def test_patient_profile_displays_and_updates_medical_information(client, monkeypatch):
    snapshot = merge_snapshot()
    snapshot["patients"][0]["medical"]["insurance_id"] = 31
    snapshot["patients"][0]["medical"].update({
        "medicare_number": "1112223334",
        "medicare_individual_reference_number": "2",
        "medicare_expiry_date": "2031-12-31",
        "private_insurance": 1,
        "p_centrelink_number": "CL-321",
    })
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)
    monkeypatch.setattr(frontend_app, "get_live_snapshot", lambda include_inactive=False: snapshot)
    monkeypatch.setattr(frontend_app, "generate_summary", lambda _patient, _admissions: "Patient summary")
    update = Mock(return_value=True)
    monkeypatch.setattr(frontend_app, "_api_update", update)

    page = client.get("/patient/1")
    response = client.post("/patient/1", data={
        "profile_action": "medical",
        "medicare_number": "9876543210",
        "medicare_individual_reference_number": "4",
        "medicare_expiry_date": "2032-12-31",
        "private_insurance": "0",
        "p_centrelink_number": "CL-654",
    })

    assert b"Medical Information" in page.data
    assert b"1112223334" in page.data
    assert response.status_code == 200
    assert b"Medical information updated" in response.data
    assert update.call_args.args[0] == "/api/patients/medical-information/31"
    assert update.call_args.args[1] == {
        "medicare_number": "9876543210",
        "medicare_individual_reference_number": "4",
        "medicare_expiry_date": "2032-12-31",
        "private_insurance": 0,
        "p_centrelink_number": "CL-654",
    }


def test_patient_contacts_are_read_only_until_edit_selected(client, monkeypatch):
    snapshot = {
        "patients": [
            {
                "patient_id": 1,
                "p_first_name": "Alex",
                "p_last_name": "Patient",
                "p_date_of_birth": "2000-01-01",
                "patient_status": "Active",
                "p_title": "Mr",
                "p_assigned_sex": "Male",
                "p_mobile": "0400000000",
                "p_email_address": None,
                "address": {},
                "medical": {},
                "contacts": [
                    {
                        "contact_id": 7,
                        "contact_first_name": "Casey",
                        "contact_last_name": "Contact",
                        "contact_date_of_birth": "1980-01-01",
                        "contact_relationship": "Parent",
                        "contact_mobile": "0411111111",
                        "contact_email": "casey@example.com",
                        "contact_address": "Main Street",
                    }
                ],
                "admin_notes": [],
            }
        ],
        "admissions": [],
    }
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)

    response = client.get("/patient/1")

    assert response.status_code == 200
    assert b"Casey" in response.data
    assert b'id="edit-contact-7"' in response.data
    assert b'id="edit-contact-7" class="form-grid profile-form contact-form" method="post" hidden' in response.data
    assert b'id="admin-note-overlay"' in response.data
    assert b'class="patient-summary-sidebar"' in response.data
    assert b'hx-target="#patient-summary"' in response.data


def test_receptionist_search_defaults_to_active_only(client, monkeypatch):
    snapshot = {
        "patients": [
            {"patient_id": 1, "p_first_name": "Active", "p_last_name": "Patient", "patient_status": "Active", "address": {}},
            {"patient_id": 2, "p_first_name": "Inactive", "p_last_name": "Patient", "patient_status": "Inactive", "address": {}},
        ],
        "admissions": [],
    }
    monkeypatch.setattr(frontend_app, "get_seed_snapshot", lambda: snapshot)

    response = client.get("/search")

    assert response.status_code == 200
    assert b"Active Patient" in response.data
    assert b"Inactive Patient" not in response.data
