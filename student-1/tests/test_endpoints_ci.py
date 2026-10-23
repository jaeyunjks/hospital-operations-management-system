"""Post-commit CI/CD endpoint tests for two Student 1 backend/API functions.

These run in ``student-1.yml`` against the real backend container, which is
connected to a freshly seeded database container.

1. ``POST /api/admissions/emergency`` - emergency admission workflow.
2. ``POST /api/patients/merge`` - duplicate-profile merge validation.

Set ``STUDENT1_BACKEND_URL`` (for example ``http://localhost:5100``) to run
them; without it they are skipped, so the unit-test runs stay self-contained.
"""
import json
import os
import unittest
import urllib.error
import urllib.request

BACKEND_URL = os.environ.get("STUDENT1_BACKEND_URL", "").rstrip("/")


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    if not BACKEND_URL.startswith(("http://", "https://")):
        raise ValueError("STUDENT1_BACKEND_URL must be an http(s) URL")
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        f"{BACKEND_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - scheme checked above
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


@unittest.skipUnless(BACKEND_URL, "STUDENT1_BACKEND_URL is not set")
class EmergencyAdmissionEndpointTests(unittest.TestCase):
    def test_complete_identity_creates_confirmed_emergency_admission(self):
        status, body = call("POST", "/api/admissions/emergency", {
            "identity": {
                "first_name": "Ada", "last_name": "Lovelace", "date_of_birth": "1990-01-01",
                "medicare_number": "2123456701", "patient_id": 1,
            },
            "patient_id": 1,
            "capacity_id": "ED-1",
        })

        self.assertEqual(status, 200)
        self.assertFalse(body["identity_review_required"])
        self.assertEqual(body["admission"]["priority"], "Emergency")
        self.assertEqual(body["admission"]["data_quality_flag"], "confirmed")
        self.assertEqual(body["capacity"]["capacity_id"], "ED-1")

    def test_partial_identity_is_provisional_and_needs_review(self):
        status, body = call("POST", "/api/admissions/emergency", {"identity": {"first_name": "Unknown"}})

        self.assertEqual(status, 200)
        self.assertTrue(body["identity_review_required"])
        self.assertEqual(body["admission"]["data_quality_flag"], "provisional")
        self.assertEqual(body["provisional_patient"]["last_name"], "Unknown")

    def test_missing_identity_is_rejected(self):
        status, body = call("POST", "/api/admissions/emergency", {})

        self.assertEqual(status, 400)
        self.assertIn("identity", body["error"].lower())


@unittest.skipUnless(BACKEND_URL, "STUDENT1_BACKEND_URL is not set")
class PatientMergeEndpointTests(unittest.TestCase):
    def test_missing_profile_ids_are_rejected(self):
        status, body = call("POST", "/api/patients/merge", {})

        self.assertEqual(status, 400)
        self.assertIn("Source and Duplicate", body["error"])

    def test_same_profile_cannot_be_merged_into_itself(self):
        status, body = call("POST", "/api/patients/merge", {"source_id": 1, "duplicate_id": 1})

        self.assertEqual(status, 400)
        self.assertIn("different", body["error"])

    def test_unknown_profiles_are_rejected(self):
        status, body = call("POST", "/api/patients/merge", {"source_id": 999999, "duplicate_id": 999998})

        self.assertEqual(status, 400)
        self.assertIn("must exist and be active", body["error"])

    def test_profile_not_awaiting_identity_review_is_rejected(self):
        status, body = call("POST", "/api/patients/merge", {"source_id": 1, "duplicate_id": 2})

        self.assertEqual(status, 400)
        self.assertIn("not awaiting emergency identity review", body["error"])
