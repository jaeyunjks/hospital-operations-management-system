from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app import create_app
from backend.routes import multi_agent_endpoints
from backend.services.admission_scheduling import (
    SchedulingInputError,
    build_review_evidence,
    follow_up_request,
)
from backend.services.multi_agent_client import (
    MultiAgentClient,
    MultiAgentDisabled,
    MultiAgentInvalidResponse,
)


WORKFLOW_ID = "90a3d71d-90cb-49dc-8728-296445ed85b1"


def request_window(**overrides):
    return {
        "date": (date.today() + timedelta(days=1)).isoformat(),
        "duration_minutes": 15,
        **overrides,
    }


def workflow(status="in_progress"):
    return {
        "schema_version": "1.0",
        "workflow_id": WORKFLOW_ID,
        "status": status,
        "task": "Review admission scheduling options.",
        "planner": None,
        "worker": None,
        "reviewer": None,
        "human_review": None,
        "final_result": None,
    }


def test_scheduling_evidence_marks_only_recorded_overlaps_and_excludes_patient_data():
    tomorrow = date.today() + timedelta(days=1)
    task, evidence = build_review_evidence(
        request_window(),
        [
            {
                "patient_id": 91,
                "admission_date": f"{tomorrow.isoformat()}T09:00",
                "admission_end": f"{tomorrow.isoformat()}T09:30",
                "admission_status": "Pending",
            },
            {
                "patient_id": 92,
                "admission_date": f"{tomorrow.isoformat()}T09:00",
                "admission_end": f"{tomorrow.isoformat()}T09:30",
                "admission_status": "Cancelled",
            },
        ],
    )

    assert "not confirmed capacity" in task
    assert [option["label"] for option in evidence["options"]] == [
        "08:00-08:15", "10:00-10:15", "11:00-11:15",
    ]
    assert evidence["candidate_times_found"] == 10
    assert evidence["candidate_times_included"] == 3
    assert evidence["objective"]["target"] == 1
    assert all("patient_id" not in option for option in evidence["options"])
    assert "patient_id" not in str(evidence)
    assert evidence["constraints"][-2]["type"] == "approval"
    assert evidence["constraints"][-1]["type"] == "safety"


@pytest.mark.parametrize(
    "changes",
    [
        {"duration_minutes": 10},
        {"duration_minutes": 600},
        {"duration_minutes": 480, "date": "2000-01-01"},
    ],
)
def test_scheduling_evidence_rejects_invalid_or_too_narrow_windows(changes):
    with pytest.raises(SchedulingInputError):
        build_review_evidence(request_window(**changes), [])


def test_planner_facing_evidence_keys_are_top_level():
    _, evidence = build_review_evidence(request_window(), [])

    assert {
        "date", "clinic_hours", "duration_minutes",
        "pending_or_active_admission_count", "schedule_evidence_complete",
    } <= set(evidence)


def test_longer_durations_must_finish_by_closing_time():
    _, evidence = build_review_evidence(
        request_window(duration_minutes=120), [], [f"{h:02d}:00-{h + 2:02d}:00" for h in range(8, 16)]
    )

    assert [option["label"] for option in evidence["options"]] == ["16:00-18:00"]


def test_conflict_free_times_are_chosen_first_and_overlaps_are_flagged():
    tomorrow = date.today() + timedelta(days=1)
    _, evidence = build_review_evidence(
        request_window(duration_minutes=60),
        [{
            "admission_date": f"{tomorrow.isoformat()}T08:00",
            "admission_end": f"{tomorrow.isoformat()}T09:00",
            "admission_status": "Pending",
        }],
    )

    assert [option["label"] for option in evidence["options"]] == [
        "09:00-10:00", "10:00-11:00", "11:00-12:00",
    ]
    assert not any(
        option["attributes"]["has_recorded_admission_overlap"]
        for option in evidence["options"]
    )


def test_follow_up_batch_excludes_already_reviewed_times_until_none_remain():
    _, first = build_review_evidence(request_window(duration_minutes=60), [])
    payload, reviewed = follow_up_request(first)
    _, second = build_review_evidence(payload, [], reviewed)
    payload, reviewed = follow_up_request(second)
    _, third = build_review_evidence(payload, [], reviewed)
    payload, reviewed = follow_up_request(third)
    _, fourth = build_review_evidence(payload, [], reviewed)
    payload, reviewed = follow_up_request(fourth)

    labels = [o["label"] for e in (first, second, third, fourth) for o in e["options"]]
    assert len(labels) == len(set(labels)) == 10
    assert len(fourth["options"]) == 1
    with pytest.raises(SchedulingInputError):
        build_review_evidence(payload, [], reviewed)


def test_partially_accept_reviews_three_other_times(monkeypatch):
    _, first = build_review_evidence(request_window(duration_minutes=60), [])
    created = []

    class FakeClient:
        def get_workflow(self, workflow_id):
            return {**workflow("awaiting_human_review"), "submitted_evidence": first}

        def submit_review(self, workflow_id, decision, comment):
            assert decision == "partially_accept"
            return workflow("partially_accepted")

        def create_workflow(self, task, evidence):
            created.append(evidence)
            return {"workflow_id": "next-id"}

    monkeypatch.setattr(multi_agent_endpoints, "_client", FakeClient)
    monkeypatch.setattr(multi_agent_endpoints.database_client, "list_admissions", lambda: [])

    response = create_app().test_client().post(
        f"/api/multi-agent/admission-scheduling/{WORKFLOW_ID}/review",
        json={"decision": "partially_accept", "comment": ""},
        headers={"X-HOMS-Role": "Receptionist"},
    )

    assert response.status_code == 200
    assert response.json["data"]["next_workflow_id"] == "next-id"
    new_labels = [o["label"] for o in created[0]["options"]]
    assert len(new_labels) == 3
    assert not set(new_labels) & {o["label"] for o in first["options"]}


def test_incomplete_admission_times_make_every_option_ineligible():
    tomorrow = date.today() + timedelta(days=1)
    _, evidence = build_review_evidence(
        request_window(),
        [{
            "admission_date": f"{tomorrow.isoformat()}T08:00",
            "admission_end": None,
            "admission_status": "Active",
        }],
    )

    assert all(
        not option["attributes"]["schedule_evidence_complete"]
        for option in evidence["options"]
    )


def test_start_endpoint_sends_student1_evidence_to_shared_server(monkeypatch):
    captured = {}

    class FakeClient:
        def create_workflow(self, task, evidence):
            captured.update(task=task, evidence=evidence)
            return workflow()

    monkeypatch.setattr(multi_agent_endpoints, "MULTI_AGENT_ENABLED", True)
    monkeypatch.setattr(
        multi_agent_endpoints.database_client,
        "list_admissions",
        lambda: {"success": True, "data": []},
    )
    monkeypatch.setattr(multi_agent_endpoints, "_client", FakeClient)
    response = create_app().test_client().post(
        "/api/multi-agent/admission-scheduling",
        json=request_window(),
        headers={"X-HOMS-Role": "Receptionist"},
    )

    assert response.status_code == 202
    assert response.json["data"]["workflow_id"] == WORKFLOW_ID
    assert captured["evidence"]["evidence_type"] == "student1_admission_scheduling"


def test_start_endpoint_requires_reception_or_admin_before_reading_admissions(monkeypatch):
    list_admissions = Mock()
    monkeypatch.setattr(multi_agent_endpoints, "MULTI_AGENT_ENABLED", True)
    monkeypatch.setattr(multi_agent_endpoints.database_client, "list_admissions", list_admissions)

    response = create_app().test_client().post(
        "/api/multi-agent/admission-scheduling",
        json=request_window(),
        headers={"X-HOMS-Role": "Doctor"},
    )

    assert response.status_code == 403
    list_admissions.assert_not_called()


def test_disabled_start_endpoint_does_not_read_admissions(monkeypatch):
    list_admissions = Mock()
    monkeypatch.setattr(multi_agent_endpoints, "MULTI_AGENT_ENABLED", False)
    monkeypatch.setattr(multi_agent_endpoints.database_client, "list_admissions", list_admissions)

    response = create_app().test_client().post(
        "/api/multi-agent/admission-scheduling",
        json=request_window(),
        headers={"X-HOMS-Role": "Receptionist"},
    )

    assert response.status_code == 503
    assert response.json["data"]["error"] == "multi_agent_disabled"
    list_admissions.assert_not_called()


def test_workflow_get_and_human_review_are_forwarded(monkeypatch):
    class FakeClient:
        def get_workflow(self, workflow_id):
            assert workflow_id == WORKFLOW_ID
            return workflow("awaiting_human_review")

        def submit_review(self, workflow_id, decision, comment):
            assert workflow_id == WORKFLOW_ID
            assert (decision, comment) == ("approve", "Reviewed")
            return workflow("approved")

    monkeypatch.setattr(multi_agent_endpoints, "_client", FakeClient)
    client = create_app().test_client()
    headers = {"X-HOMS-Role": "Receptionist"}

    loaded = client.get(
        f"/api/multi-agent/admission-scheduling/{WORKFLOW_ID}", headers=headers
    )
    finalized = client.post(
        f"/api/multi-agent/admission-scheduling/{WORKFLOW_ID}/review",
        json={"decision": "approve", "comment": "Reviewed"},
        headers=headers,
    )

    assert loaded.status_code == 200
    assert loaded.json["data"]["status"] == "awaiting_human_review"
    assert finalized.status_code == 200
    assert finalized.json["data"]["status"] == "approved"


def test_review_endpoint_requires_comment_for_corrections(monkeypatch):
    response = create_app().test_client().post(
        f"/api/multi-agent/admission-scheduling/{WORKFLOW_ID}/review",
        json={"decision": "correct", "comment": " "},
        headers={"X-HOMS-Role": "Receptionist"},
    )

    assert response.status_code == 400


def test_multi_agent_client_is_disabled_without_network_access(monkeypatch):
    network = Mock()
    monkeypatch.setattr("backend.services.multi_agent_client.requests.request", network)
    client = MultiAgentClient(False, "http://127.0.0.1:8200", 2)

    with pytest.raises(MultiAgentDisabled):
        client.create_workflow("task", {"options": []})

    network.assert_not_called()


def test_multi_agent_client_rejects_unexpected_workflow_response(monkeypatch):
    response = SimpleNamespace(status_code=202, json=lambda: {"unexpected": True})
    monkeypatch.setattr(
        "backend.services.multi_agent_client.requests.request",
        Mock(return_value=response),
    )

    with pytest.raises(MultiAgentInvalidResponse):
        MultiAgentClient(True, "http://127.0.0.1:8200", 2).create_workflow(
            "task", {"options": []}
        )


def test_open_ended_admissions_on_other_dates_do_not_block_options():
    _, evidence = build_review_evidence(
        request_window(),
        [
            {"admission_date": "2026-08-03", "admission_end": None, "admission_status": "Active"},
            {"admission_date": "2026-08-12", "admission_end": None, "admission_status": "Pending"},
        ],
    )

    assert all(
        option["attributes"]["schedule_evidence_complete"]
        and not option["attributes"]["has_recorded_admission_overlap"]
        for option in evidence["options"]
    )
    assert any("no end time" in item for item in evidence["unknowns"])
