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

    assert frontend_app.generate_ai_summary(patient) == "Patient requires interpreter support."


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
