"""Bed shortage workflow (Architecture v2.2, section 5.2)."""

from conftest import data, error


def open_case(api, **overrides):
    payload = {
        "patient_id": 7001,
        "required_care_category": "Long-term",
        "required_ward": "General Ward 1",
        "urgency": "High",
        "holding_location": "Emergency Department bay 2",
    }
    payload.update(overrides)
    return data(api.post("/api/shortage-cases", json=payload))


def test_case_opens_with_the_requirement_recorded(api):
    case = open_case(api)
    assert case["status"] == "Open"
    assert case["urgency"] == "High"
    assert case["holding_location"] == "Emergency Department bay 2"
    assert case["opened_by"]


def test_invalid_urgency_is_rejected(api):
    response = api.post("/api/shortage-cases", json={
        "patient_id": 7002, "required_care_category": "Long-term", "urgency": "Whenever",
    })
    assert response.status_code == 400


def test_options_are_offered_in_priority_order(api):
    case = open_case(api)
    options = data(api.get("/api/shortage-cases/{}/options".format(case["case_id"])))["options"]
    kinds = [o["kind"] for o in options]
    assert kinds == sorted(
        kinds,
        key=lambda k: {"available_now": 0, "pending_cleaning": 1,
                       "alternative_ward": 2, "escalate": 3}[k],
    )
    assert kinds[-1] == "escalate", "escalation must always be available"


def test_a_decision_requires_a_reason(api):
    case = open_case(api)
    response = api.put("/api/shortage-cases/{}/decide".format(case["case_id"]),
                       json={"chosen_option": "Allocate W1-102-A"})
    assert response.status_code == 400
    assert "decision_reason" in error(response)


def test_choosing_a_bed_reserves_it_but_does_not_occupy_it(api):
    case = open_case(api)
    data(api.put("/api/shortage-cases/{}/decide".format(case["case_id"]), json={
        "chosen_option": "Allocate W1-102-A",
        "decision_reason": "Private room free now",
        "resolved_bed_id": 19,
    }))
    assert data(api.get("/api/beds/19"))["status"] == "reserved"


def test_an_occupied_bed_cannot_be_reserved(api):
    case = open_case(api)
    response = api.put("/api/shortage-cases/{}/decide".format(case["case_id"]), json={
        "chosen_option": "Take ICU-01-A", "decision_reason": "Nothing else",
        "resolved_bed_id": 13,
    })
    assert response.status_code == 409


def test_escalation_closes_the_case_with_a_reason(api):
    case = open_case(api, urgency="Critical")
    updated = data(api.put("/api/shortage-cases/{}/decide".format(case["case_id"]), json={
        "chosen_option": "Escalate to on-call operations manager",
        "decision_reason": "No compatible bed in the hospital",
        "escalate": True,
    }))
    assert updated["status"] == "Escalated"
    assert updated["resolved_at"]


def test_cancelling_a_case_releases_its_reserved_bed(api):
    case = open_case(api)
    data(api.put("/api/shortage-cases/{}/decide".format(case["case_id"]), json={
        "chosen_option": "Allocate W1-102-A", "decision_reason": "Free now",
        "resolved_bed_id": 19,
    }))
    data(api.put("/api/shortage-cases/{}/cancel".format(case["case_id"]),
                 json={"decision_reason": "Patient discharged from ED"}))
    assert data(api.get("/api/beds/19"))["status"] == "available"


def test_a_resolved_case_cannot_be_decided_again(api):
    case = open_case(api)
    data(api.put("/api/shortage-cases/{}/resolve".format(case["case_id"]),
                 json={"decision_reason": "Placed", "resolved_bed_id": 19}))
    response = api.put("/api/shortage-cases/{}/decide".format(case["case_id"]),
                       json={"chosen_option": "x", "decision_reason": "y"})
    assert response.status_code == 409
