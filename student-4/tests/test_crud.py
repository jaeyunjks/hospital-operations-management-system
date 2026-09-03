"""CRUD coverage for the three catalogue resources, including deletion."""

import pytest

from conftest import data, error


def test_read_all_room_types(api):
    assert len(data(api.get("/api/room-types"))) >= 10


def test_create_read_update_room(api):
    created = data(api.post("/api/rooms", json={
        "room_number": "W3-301", "ward": "General Ward 3",
        "floor": "Level 6", "type_id": 8,
    }))
    room_id = created["room_id"]
    assert created["status"] == "Available"

    fetched = data(api.get("/api/rooms/{}".format(room_id)))
    assert fetched["room_number"] == "W3-301"
    assert fetched["type"]["care_category"] == "Long-term"

    updated = data(api.put("/api/rooms/{}".format(room_id), json={"notes": "Corner room"}))
    assert updated["notes"] == "Corner room"


def test_duplicate_room_number_is_rejected(api):
    response = api.post("/api/rooms", json={
        "room_number": "OT-01", "ward": "Surgical Suite", "floor": "Level 2", "type_id": 1,
    })
    assert response.status_code == 400
    assert "constraint" in error(response).lower()


def test_unknown_room_type_is_rejected(api):
    response = api.post("/api/rooms", json={
        "room_number": "ZZ-999", "ward": "Nowhere", "floor": "Level 9", "type_id": 999,
    })
    assert response.status_code == 400


@pytest.mark.parametrize("field", ["room_number", "ward", "floor", "type_id"])
def test_missing_required_room_field_is_rejected(api, field):
    payload = {"room_number": "T-1", "ward": "W", "floor": "F", "type_id": 8}
    payload.pop(field)
    response = api.post("/api/rooms", json=payload)
    assert response.status_code == 400
    assert field in error(response)


def test_room_type_in_use_cannot_be_deleted(api):
    response = api.delete("/api/room-types/1")
    assert response.status_code == 400
    assert "cannot be deleted" in error(response)


def test_unused_room_type_can_be_deleted(api):
    created = data(api.post("/api/room-types", json={
        "type_name": "Observation Pod", "care_category": "Short-term",
        "default_capacity": 1,
    }))
    deleted = data(api.delete("/api/room-types/{}".format(created["type_id"])))
    assert deleted["deleted"] == created["type_id"]
    assert api.get("/api/room-types/{}".format(created["type_id"])).status_code == 404


def test_bed_delete_is_soft(api):
    """Retiring a bed keeps the row so history stays valid."""
    result = data(api.delete("/api/beds/24"))
    assert result["retired"]["status"] == "maintenance"
    assert data(api.get("/api/beds/24"))["bed_id"] == 24


def test_occupied_bed_cannot_be_retired(api):
    response = api.delete("/api/beds/13")  # ICU-01-A is occupied
    assert response.status_code == 409


def test_room_with_occupied_beds_cannot_go_out_of_service(api):
    response = api.put("/api/rooms/7/status", json={"status": "Out of Service"})
    assert response.status_code == 409


def test_room_status_can_be_set_to_cleaning(api):
    updated = data(api.put("/api/rooms/12/status", json={"status": "Cleaning"}))
    assert updated["status"] == "Cleaning"


def test_invalid_room_status_is_rejected(api):
    response = api.put("/api/rooms/12/status", json={"status": "Painting"})
    assert response.status_code == 400


def test_availability_filters_by_care_category(api):
    rows = data(api.get("/api/rooms/availability?care_category=Surgical&bed_status=available"))
    assert rows
    assert all(r["care_category"] == "Surgical" for r in rows)
    assert all(r["bed_status"] == "available" for r in rows)


def test_missing_record_returns_404(api):
    assert api.get("/api/rooms/9999").status_code == 404
