"""Patient transfer between beds — persona 3's routine task."""

from conftest import data, error


def admit(api, bed_id, patient_id=6001):
    return data(api.post("/api/arrangements", json={
        "bed_id": bed_id, "patient_id": patient_id, "admission_id": 9100,
        "purpose": "Inpatient stay", "start_time": "2026-09-01 08:00",
        "status": "In Progress",
    }))


def test_transfer_moves_the_patient_and_links_the_records(api):
    stay = admit(api, 19)
    result = data(api.post("/api/arrangements/{}/transfer".format(stay["arrangement_id"]),
                           json={"to_bed_id": 24, "reason": "Rehabilitation programme"}))

    assert result["from"]["status"] == "Completed"
    assert result["to"]["status"] == "In Progress"
    assert result["to"]["transferred_from_id"] == stay["arrangement_id"]
    assert result["to"]["patient_id"] == stay["patient_id"]


def test_transfer_frees_the_old_bed_and_fills_the_new_one(api):
    stay = admit(api, 19)
    data(api.post("/api/arrangements/{}/transfer".format(stay["arrangement_id"]),
                  json={"to_bed_id": 24}))
    assert data(api.get("/api/beds/19"))["status"] == "available"
    assert data(api.get("/api/beds/24"))["status"] == "occupied"


def test_transfer_adopts_the_target_rooms_care_category(api):
    """Moving from a Long-term room to a Surgical bay re-categorises."""
    stay = admit(api, 19)  # Single Inpatient Room, Long-term
    result = data(api.post("/api/arrangements/{}/transfer".format(stay["arrangement_id"]),
                           json={"to_bed_id": 5}))  # REC-01-B, Surgical
    assert stay["care_category"] == "Long-term"
    assert result["to"]["care_category"] == "Surgical"


def test_transfer_to_the_same_bed_is_rejected(api):
    stay = admit(api, 19)
    response = api.post("/api/arrangements/{}/transfer".format(stay["arrangement_id"]),
                        json={"to_bed_id": 19})
    assert response.status_code == 400


def test_transfer_to_an_occupied_bed_is_rejected(api):
    stay = admit(api, 19)
    response = api.post("/api/arrangements/{}/transfer".format(stay["arrangement_id"]),
                        json={"to_bed_id": 13})  # ICU-01-A, occupied
    assert response.status_code == 409


def test_only_an_in_progress_stay_can_be_transferred(api):
    scheduled = data(api.post("/api/arrangements", json={
        "bed_id": 19, "patient_id": 6002, "purpose": "Inpatient stay",
        "start_time": "2026-10-01 08:00",
    }))
    response = api.post("/api/arrangements/{}/transfer".format(scheduled["arrangement_id"]),
                        json={"to_bed_id": 24})
    assert response.status_code == 409
    assert "in-progress" in error(response)
