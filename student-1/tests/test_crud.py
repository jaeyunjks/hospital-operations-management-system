import sqlite3
from pathlib import Path
from datetime import date, timedelta

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


def test_patient_stores_emergency_origin_and_manual_review_decision(database_client):
    response = database_client.patch("/api/patients/1", json={
        "emergency_override": 1,
        "identity_review_status": "Duplicate",
        "identity_review_candidate_id": 2,
        "identity_reviewed_by": "Receptionist",
        "identity_reviewed_at": "2026-10-01T12:00:00",
    })

    assert response.status_code == 200
    patient = response.get_json()
    assert patient["emergency_override"] == 1
    assert patient["identity_review_status"] == "Duplicate"
    assert patient["identity_review_candidate_id"] == 2
    assert patient["identity_reviewed_by"] == "Receptionist"


def test_patient_merge_atomically_reconciles_related_records(database_client):
    source_id, duplicate_id = 1, 2
    source = database_client.get(f"/api/patients/{source_id}").get_json()
    duplicate = database_client.get(f"/api/patients/{duplicate_id}").get_json()
    assert database_client.patch(f"/api/patients/{duplicate_id}", json={
        "emergency_override": 1,
        "identity_review_status": "Pending",
    }).status_code == 200

    source_address = database_client.get(f"/api/patient-addresses?patient_id={source_id}").get_json()[0]
    source_medical = database_client.get(f"/api/patient-medical-information?patient_id={source_id}").get_json()[0]
    duplicate_contact_response = database_client.post("/api/patient-contacts", json={
        "patient_id": duplicate_id,
        "contact_first_name": "Merge",
        "contact_last_name": "Contact",
        "contact_date_of_birth": "1980-01-01",
        "contact_relationship": "Sibling",
        "contact_address": "1 Main Street",
        "contact_mobile": "0400000000",
    })
    contact_id = duplicate_contact_response.get_json()["contact_id"]
    future_date = (date.today() + timedelta(days=7)).isoformat()
    duplicate_admission = database_client.post("/api/admissions", json={
        "patient_id": duplicate_id,
        "admission_date": f"{future_date}T13:00",
        "admission_end": f"{future_date}T13:15",
        "admission_status": "Pending",
    }).get_json()
    note = database_client.post("/api/patient-admin-notes", json={
        "patient_id": duplicate_id,
        "note_text": "Emergency identity was incomplete.",
        "created_at": "2026-10-01T09:00:00",
    })
    assert note.status_code == 201

    reviewed_fields = [
        f"{group}:{field}"
        for group, fields in (
            ("patient", database_service.MERGE_PATIENT_FIELDS),
            ("address", database_service.MERGE_ADDRESS_FIELDS),
            ("medical", database_service.MERGE_MEDICAL_FIELDS),
        )
        for field in fields
    ]
    response = database_client.post("/api/patients/merge", json={
        "source_id": source_id,
        "duplicate_id": duplicate_id,
        "source": {
            "patient": {field: source.get(field) for field in database_service.MERGE_PATIENT_FIELDS},
            "address": {field: source_address[field] for field in database_service.MERGE_ADDRESS_FIELDS},
            "medical": {field: source_medical[field] for field in database_service.MERGE_MEDICAL_FIELDS},
        },
        "reviewed_fields": reviewed_fields,
        "reviewed_by": "Receptionist",
        "contact_ids": [contact_id],
        "admission_ids": [duplicate_admission["admission_id"]],
    })

    assert response.status_code == 200
    assert response.get_json()["merged"] is True
    assert database_client.get(f"/api/patients/{duplicate_id}").status_code == 400
    merged_profile = database_client.get(f"/api/patients/{duplicate_id}?includeInactive=true").get_json()
    assert merged_profile["patient_status"] == "Merged"
    assert merged_profile["deactivated_at"]
    source_contacts = database_client.get(f"/api/patient-contacts?patient_id={source_id}").get_json()
    assert any(contact["contact_id"] != contact_id and contact["contact_first_name"] == "Merge" for contact in source_contacts)
    source_admissions = database_client.get(f"/api/admissions?patient_id={source_id}").get_json()
    assert any(row["admission_id"] == duplicate_admission["admission_id"] for row in source_admissions)
    source_notes = database_client.get(f"/api/patient-admin-notes?patient_id={source_id}").get_json()
    assert any(note["note_text"] == "[Merged from duplicate MRN-2] Emergency identity was incomplete." for note in source_notes)
    assert any(note["note_text"] == "PATIENT PROFILE 2 MERGED INTO 1" for note in source_notes)


def test_patient_merge_rejects_unreviewed_fields_without_partial_changes(database_client):
    source_before = database_client.get("/api/patients/1").get_json()
    assert database_client.patch("/api/patients/2", json={
        "emergency_override": 1,
        "identity_review_status": "Pending",
    }).status_code == 200

    response = database_client.post("/api/patients/merge", json={
        "source_id": 1,
        "duplicate_id": 2,
        "source": {},
        "reviewed_fields": [],
    })

    assert response.status_code == 400
    assert "Review every mismatched" in response.get_json()["error"]
    assert database_client.get("/api/patients/1").get_json()["p_first_name"] == source_before["p_first_name"]
    assert database_client.get("/api/patients/2").get_json()["identity_review_status"] == "Pending"


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


def test_medical_information_can_be_updated(database_client):
    response = database_client.patch("/api/patient-medical-information/1", json={
        "medicare_number": "9876543210",
        "medicare_individual_reference_number": "4",
        "medicare_expiry_date": "2032-12-31",
        "private_insurance": 1,
        "p_centrelink_number": "CL-654",
    })

    assert response.status_code == 200
    assert response.get_json()["medicare_number"] == "9876543210"
    assert response.get_json()["private_insurance"] == 1


def test_admission_crud_enforces_quarter_hour_intervals_and_statuses(database_client):
    future_date = (date.today() + timedelta(days=7)).isoformat()
    valid = database_client.post(
        "/api/admissions",
        json={
            "patient_id": 1,
            "admission_date": f"{future_date}T13:00",
            "admission_end": f"{future_date}T13:15",
            "admission_status": "Pending",
        },
    )

    assert valid.status_code == 201

    invalid_interval = database_client.post(
        "/api/admissions",
        json={
            "patient_id": 1,
            "admission_date": f"{future_date}T13:00",
            "admission_end": f"{future_date}T13:10",
            "admission_status": "Pending",
        },
    )
    assert invalid_interval.status_code == 400

    invalid_timezone = database_client.post(
        "/api/admissions",
        json={
            "patient_id": 1,
            "admission_date": f"{future_date}T13:00+00:00",
            "admission_end": f"{future_date}T13:15+00:00",
            "admission_status": "Pending",
        },
    )
    assert invalid_timezone.status_code == 400

    invalid_status = database_client.post(
        "/api/admissions",
        json={
            "patient_id": 1,
            "admission_date": f"{future_date}T13:00",
            "admission_end": f"{future_date}T14:00",
            "admission_status": "Active",
        },
    )
    assert invalid_status.status_code == 400

    completed = database_client.post(
        "/api/admissions",
        json={
            "patient_id": 1,
            "admission_date": f"{(date.today() - timedelta(days=1)).isoformat()}T13:00",
            "admission_end": f"{(date.today() - timedelta(days=1)).isoformat()}T14:00",
            "admission_status": "Completed",
        },
    )
    assert completed.status_code == 201
    assert completed.get_json()["discharge_date"]


def test_existing_database_migrates_admission_end(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.db"
    database_directory = Path(database_service.__file__).resolve().parent
    schema = (database_directory / "schema.sql").read_text().replace("    admission_end TEXT,\n", "")
    connection = sqlite3.connect(database_path)
    connection.executescript(schema)
    connection.executescript((database_directory / "seed_data.sql").read_text())
    connection.close()
    monkeypatch.setattr(database_module, "DB_PATH", database_path)

    database_service.ensure_database_ready()

    connection = sqlite3.connect(database_path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(admissions)")}
    patient_columns = {row[1] for row in connection.execute("PRAGMA table_info(patients)")}
    connection.close()
    assert "admission_end" in columns
    assert {
        "emergency_override",
        "identity_review_status",
        "identity_review_candidate_id",
        "identity_reviewed_by",
        "identity_reviewed_at",
    } <= patient_columns


def test_existing_database_migrates_patient_status_to_allow_merged(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy-status.db"
    database_directory = Path(database_service.__file__).resolve().parent
    schema = (database_directory / "schema.sql").read_text().replace(
        "            'Transferred',\n            'Merged'\n",
        "            'Transferred'\n",
    )
    connection = sqlite3.connect(database_path)
    connection.executescript(schema)
    connection.executescript((database_directory / "seed_data.sql").read_text())
    connection.close()
    monkeypatch.setattr(database_module, "DB_PATH", database_path)

    database_service.ensure_database_ready()

    connection = sqlite3.connect(database_path)
    connection.execute(
        "UPDATE patients SET patient_status = 'Merged', deactivated_at = '2026-10-01' WHERE patient_id = 1"
    )
    connection.commit()
    migrated = connection.execute(
        "SELECT patient_status, deactivated_at FROM patients WHERE patient_id = 1"
    ).fetchone()
    connection.close()
    assert migrated == ("Merged", "2026-10-01")
