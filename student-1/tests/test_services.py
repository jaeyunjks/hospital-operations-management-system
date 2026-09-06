import sqlite3

from backend.app import create_app
from backend.auth import ROLE_MANAGER, ROLE_RECEPTIONIST, identity_from_request
from database.init_database import build, healthCheck


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


def test_database_build_creates_seeded_healthy_database(tmp_path):
    database_path = tmp_path / "patients.db"
    connection = build(database_path)
    try:
        assert healthCheck(connection)
        assert connection.execute("SELECT COUNT(*) FROM patients").fetchone()[0] >= 10
    finally:
        connection.close()

    assert sqlite3.connect(database_path).execute("SELECT 1").fetchone() == (1,)
