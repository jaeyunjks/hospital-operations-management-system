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


def test_empty_intake_shows_required_fields(client):
    response = client.post("/intake", data={})

    assert response.status_code == 200
    assert b"These fields are required" in response.data
    assert response.data.count(b'class="required-star"') >= 8


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
    monkeypatch.setattr(frontend_app, "get_live_snapshot", lambda: refreshed_snapshot)

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
    monkeypatch.setattr(frontend_app, "get_live_snapshot", frontend_app.get_seed_snapshot)
    monkeypatch.setattr(frontend_app, "generate_summary", lambda _patient, _admissions: "Updated <summary>")

    response = client.post(
        "/patient/1",
        data={"summary_action": "refresh"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.data == b"Updated &lt;summary&gt;"


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
