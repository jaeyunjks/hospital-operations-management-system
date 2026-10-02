import sqlite3

import pytest

from backend.app import create_app
from backend.auth import ROLE_MANAGER, ROLE_RECEPTIONIST, identity_from_request
from backend.routes import patients as patient_routes
from database.init_database import build, healthCheck
from unittest.mock import Mock


def test_identity_uses_request_role_and_user_id():
    app = create_app()

    with app.test_request_context(
        "/",
        headers={"X-HOMS-Role": ROLE_RECEPTIONIST, "X-HOMS-User-Id": "17"},
    ):
        identity = identity_from_request()

    assert identity == {
        "role": ROLE_RECEPTIONIST,
        "user_id": 17,
        "name": "Receptionist",
    }


def test_backend_identity_endpoint_returns_manager_identity():
    app = create_app()
    response = app.test_client().get("/api/auth/identity")

    assert response.status_code == 200
    assert response.get_json()["data"] == {
        "role": ROLE_MANAGER,
        "user_id": None,
        "name": "System Administrator",
    }


@pytest.mark.parametrize("path", ["/health", "/api/health"])
def test_backend_health_endpoints_return_ok(path):
    response = create_app().test_client().get(path)

    assert response.status_code == 200
    assert response.get_json()["data"] == {
        "status": "ok",
        "service": "student-1-backend",
    }


def test_database_health_endpoint_returns_ok(tmp_path, monkeypatch):
    import database.app as database_app

    database_path = tmp_path / "patients.db"
    connection = build(database_path)
    connection.close()
    monkeypatch.setattr(database_app.database, "DB_PATH", database_path)

    response = database_app.app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_backend_medical_information_update_proxy(monkeypatch):
    database_call = Mock(return_value=({"updated": True}, 200))
    monkeypatch.setattr(patient_routes, "_db_call", database_call)

    response = create_app().test_client().patch(
        "/api/patients/medical-information/31",
        json={"medicare_number": "9876543210"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"updated": True}
    database_call.assert_called_once_with(
        "PATCH",
        "/api/patient-medical-information/31",
        payload={"medicare_number": "9876543210"},
    )


def test_database_build_creates_seeded_healthy_database(tmp_path):
    database_path = tmp_path / "patients.db"
    connection = build(database_path)
    try:
        assert healthCheck(connection)
        assert connection.execute("SELECT COUNT(*) FROM patients").fetchone()[0] >= 10
    finally:
        connection.close()

    assert sqlite3.connect(database_path).execute("SELECT 1").fetchone() == (1,)
