import pytest

from database import app as database_service
from database import database as database_module
from database.init_database import build


@pytest.fixture
def database_client(tmp_path, monkeypatch):
    database_path = tmp_path / "patients.db"
    connection = build(database_path)
    connection.close()
    monkeypatch.setattr(database_module, "DB_PATH", database_path)
    database_service.app.config.update(TESTING=True)
    return database_service.app.test_client()


def test_patient_crud_lifecycle(database_client):
    patient = {
        "p_title": "Ms",
        "p_first_name": "Taylor",
        "p_last_name": "Created",
        "p_date_of_birth": "1995-05-05",
        "p_assigned_sex": "Female",
        "p_mobile": "0400000001",
        "p_marital_status": "Single",
        "p_first_nations_heritage": "Unknown",
        "patient_status": "Active",
        "created_at": "2026-09-06 10:00:00",
        "updated_at": "2026-09-06 10:00:00",
    }

    created = database_client.post("/api/patients", json=patient)
    assert created.status_code == 201
    patient_id = created.get_json()["patient_id"]

    fetched = database_client.get(f"/api/patients/{patient_id}")
    assert fetched.status_code == 200
    assert fetched.get_json()["p_first_name"] == "Taylor"

    updated = database_client.patch(
        f"/api/patients/{patient_id}",
        json={"p_first_name": "Updated"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["p_first_name"] == "Updated"

    deleted = database_client.delete(f"/api/patients/{patient_id}")
    assert deleted.status_code == 200
    assert deleted.get_json()["deactivated"] is True
    assert database_client.get(f"/api/patients/{patient_id}").status_code == 400

    restored = database_client.post(f"/api/patients/{patient_id}/restore")
    assert restored.status_code == 200
    assert restored.get_json()["record"]["patient_status"] == "Active"


def test_create_rejects_missing_required_patient_fields(database_client):
    response = database_client.post("/api/patients", json={"p_first_name": "Incomplete"})

    assert response.status_code == 400
    assert "Missing required field" in response.get_json()["error"]


def test_contact_crud_delete_is_permanent(database_client):
    created = database_client.post(
        "/api/patient-contacts",
        json={
            "patient_id": 1,
            "contact_first_name": "New",
            "contact_last_name": "Contact",
            "contact_date_of_birth": "1980-01-01",
            "contact_relationship": "Friend",
            "contact_address": "Somewhere",
            "contact_mobile": "0400000002",
        },
    )
    assert created.status_code == 201
    contact_id = created.get_json()["contact_id"]

    deleted = database_client.delete(f"/api/patient-contacts/{contact_id}")

    assert deleted.status_code == 200
    assert deleted.get_json() == {"deleted": True, "id": contact_id}
    assert database_client.get(f"/api/patient-contacts/{contact_id}").status_code == 400
