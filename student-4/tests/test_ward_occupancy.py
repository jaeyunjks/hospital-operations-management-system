"""Ward occupancy is a published contract other services depend on.

Staff & Shift Management consumes it, so the field names and the
arithmetic are pinned here: a change that breaks their integration
should break a test first.
"""

from conftest import data

FIELDS = {"ward", "total_beds", "occupied", "available", "reserved",
          "maintenance", "monitored_beds", "occupancy_pct", "care_categories"}


def wards(api, query=""):
    return data(api.get("/api/wards/occupancy" + query))


def test_every_ward_reports_the_agreed_fields(api):
    result = wards(api)
    assert result["wards"]
    for row in result["wards"]:
        assert FIELDS <= set(row), "missing: {}".format(FIELDS - set(row))


def test_bed_counts_add_up_within_each_ward(api):
    for row in wards(api)["wards"]:
        parts = row["occupied"] + row["available"] + row["reserved"] + row["maintenance"]
        assert parts == row["total_beds"], row["ward"]


def test_occupancy_percentage_matches_the_counts(api):
    for row in wards(api)["wards"]:
        expected = round(row["occupied"] * 100.0 / row["total_beds"], 1)
        assert row["occupancy_pct"] == expected


def test_totals_match_the_sum_of_the_wards(api):
    result = wards(api)
    assert result["totals"]["total_beds"] == sum(r["total_beds"] for r in result["wards"])
    assert result["totals"]["occupied"] == sum(r["occupied"] for r in result["wards"])


def test_a_single_ward_can_be_requested(api):
    result = wards(api, "?ward=Critical Care")
    assert len(result["wards"]) == 1
    assert result["wards"][0]["ward"] == "Critical Care"


def test_care_categories_are_listed_per_ward(api):
    row = next(r for r in wards(api)["wards"] if r["ward"] == "Critical Care")
    assert row["care_categories"] == ["Short-term"]


def test_monitored_bed_count_is_reported(api):
    """Nurse ratios depend on how many beds require monitoring."""
    row = next(r for r in wards(api)["wards"] if r["ward"] == "Critical Care")
    assert row["monitored_beds"] == row["total_beds"]


def test_occupancy_reflects_a_new_allocation(api):
    before = next(r for r in wards(api)["wards"] if r["ward"] == "General Ward 1")
    data(api.post("/api/arrangements", json={
        "bed_id": 19, "patient_id": 4001, "purpose": "Inpatient stay",
        "start_time": "2026-09-01 08:00", "status": "In Progress",
    }))
    after = next(r for r in wards(api)["wards"] if r["ward"] == "General Ward 1")
    assert after["occupied"] == before["occupied"] + 1
    assert after["available"] == before["available"] - 1
    assert after["occupancy_pct"] > before["occupancy_pct"]


def test_endpoint_does_not_require_the_ai_service(api, monkeypatch):
    """The contract must hold with Ollama absent."""
    from services import ollama_client

    def boom(*_a, **_k):
        raise ollama_client.AIUnavailable("Ollama not running")

    monkeypatch.setattr(ollama_client, "generate", boom)
    monkeypatch.setattr(ollama_client, "generate_json", boom)
    assert wards(api)["wards"]
