"""Double-booking prevention — the core rule of this feature.

A bed or operating table may hold only one active arrangement at a time.
These tests cover the boundaries as well as the obvious overlap, because
back-to-back bookings are legitimate and must not be rejected.
"""

from conftest import data, error

FREE_THEATRE_BED = 2   # OT-02-TABLE, available in the seed data
BUSY_THEATRE_BED = 1   # OT-01-TABLE, arrangement 1 runs 08:00-11:00 on 24/08


def book(api, **overrides):
    payload = {
        "bed_id": FREE_THEATRE_BED,
        "patient_id": 9001,
        "purpose": "Surgery",
        "procedure_name": "Test procedure",
        "surgeon_name": "Dr. Test",
        "start_time": "2026-09-01 08:00",
        "end_time": "2026-09-01 10:00",
    }
    payload.update(overrides)
    return api.post("/api/arrangements", json=payload)


def test_booking_a_free_window_succeeds(api):
    record = data(book(api))
    assert record["status"] == "Scheduled"
    assert record["bed_id"] == FREE_THEATRE_BED


def test_identical_window_is_rejected(api):
    book(api)
    response = book(api, patient_id=9002)
    assert response.status_code == 409
    assert "already booked" in error(response)


def test_partial_overlap_is_rejected(api):
    book(api)
    response = book(api, patient_id=9003,
                    start_time="2026-09-01 09:00", end_time="2026-09-01 11:00")
    assert response.status_code == 409


def test_enclosing_window_is_rejected(api):
    book(api)
    response = book(api, patient_id=9004,
                    start_time="2026-09-01 07:00", end_time="2026-09-01 12:00")
    assert response.status_code == 409


def test_back_to_back_booking_is_allowed(api):
    """A session starting exactly when the previous one ends is fine."""
    book(api)
    response = book(api, patient_id=9005,
                    start_time="2026-09-01 10:00", end_time="2026-09-01 12:00")
    assert response.status_code == 201


def test_open_ended_stay_blocks_later_bookings(api):
    """An inpatient stay with no end time occupies the bed indefinitely."""
    data(api.post("/api/arrangements", json={
        "bed_id": 19, "patient_id": 9006, "purpose": "Inpatient stay",
        "start_time": "2026-09-01 08:00", "status": "In Progress",
    }))
    response = api.post("/api/arrangements", json={
        "bed_id": 19, "patient_id": 9007, "purpose": "Inpatient stay",
        "start_time": "2026-12-01 08:00",
    })
    assert response.status_code == 409


def test_cancelled_arrangement_frees_the_window(api):
    created = data(book(api))
    data(api.put("/api/arrangements/{}/cancel".format(created["arrangement_id"]),
                 json={"reason": "Patient postponed"}))
    assert book(api, patient_id=9008).status_code == 201


def test_released_arrangement_frees_the_bed(api):
    created = data(api.post("/api/arrangements", json={
        "bed_id": 19, "patient_id": 9009, "purpose": "Inpatient stay",
        "start_time": "2026-09-01 08:00", "status": "In Progress",
    }))
    assert data(api.get("/api/beds/19"))["status"] == "occupied"

    data(api.put("/api/arrangements/{}/release".format(created["arrangement_id"]),
                 json={"end_time": "2026-09-02 09:00"}))
    assert data(api.get("/api/beds/19"))["status"] == "available"


def test_maintenance_bed_cannot_be_booked(api):
    response = book(api, bed_id=3)  # OT-03-TABLE, room out of service
    assert response.status_code == 409
    assert "maintenance" in error(response)


def test_rescheduling_ignores_its_own_booking(api):
    """Moving a booking must not clash with the row being moved."""
    created = data(book(api))
    response = api.put("/api/arrangements/{}".format(created["arrangement_id"]),
                       json={"start_time": "2026-09-01 08:30",
                             "end_time": "2026-09-01 10:30"})
    assert response.status_code == 200
