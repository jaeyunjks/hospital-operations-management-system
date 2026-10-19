"""Backend access to the shared Multi-Agent Server: flag, evidence, scope, review and failures."""
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from services import multi_agent_client
from services.reorder_workflow import build_evidence

WORKFLOW_ID = "a42cc1a0-917e-460e-89c0-2426772ef65a"
CANDIDATE = {"medicine_id": 1, "medicine_name": "Paracetamol 500mg", "supplier_id": 1,
             "supplier_name": "MedSupply Australia", "available_quantity": 20, "daily_usage_rate": 4.2,
             "lead_time_days": 5, "open_order_quantity": 30, "near_expiry_quantity": 5,
             "suggested_quantity": 180, "unit_price": 0.12}
STAGE = {"started_at": "2026-10-19T01:00:00Z", "completed_at": "2026-10-19T01:00:20Z",
         "output": {"goal": "Decide the Paracetamol reorder"},
         "execution": {"provider": "live_ollama", "model": "llama3.1:8b", "fallback_used": False,
                       "attempt_count": 1, "raw": "internal"}}
WORKFLOW = {"schema_version": "1.0", "workflow_id": WORKFLOW_ID, "status": "awaiting_human_review",
            "task": "Pharmacy reorder decision: Paracetamol 500mg", "planner": STAGE, "worker": STAGE,
            "reviewer": STAGE, "human_review": None, "final_result": None}
REVIEWED = {**WORKFLOW, "status": "completed",
            "human_review": {"decision": "approve", "comments": "Order it.", "timestamp": "2026-10-19T01:02:00Z"},
            "final_result": {"decision": "approve"}}
OTHER_FEATURE = {**WORKFLOW, "task": "Resolve a staffing shortage for the Emergency evening shift."}
MANAGER = {"X-HOMS-Role": "Pharmacy Manager"}


class MultiAgentTestCase(unittest.TestCase):
    enabled = True

    def setUp(self):
        self.client = app.app.test_client()
        self.calls = []
        for name, value in (("MULTI_AGENT_ENABLED", self.enabled),
                            ("MULTI_AGENT_SERVER_URL", "http://127.0.0.1:8200")):
            patcher = patch.object(multi_agent_client, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(app, "reorder_candidates", lambda fetch: [CANDIDATE])
        patcher.start()
        self.addCleanup(patcher.stop)

    def respond(self, *responses):
        """Replace the HTTP call; each item is (status, body) or an exception."""
        queue = list(responses)

        def fake(method, path, payload=None, timeout=None):
            self.calls.append((method, path, payload))
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        patcher = patch.object(multi_agent_client, "_request", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start(self, body=None):
        return self.client.post("/api/multi-agent/reorder-workflows", json=body or {"medicine_id": 1})

    def review(self, body, headers=MANAGER, workflow_id=WORKFLOW_ID):
        return self.client.post(f"/api/multi-agent/workflows/{workflow_id}/review", json=body, headers=headers)


class MultiAgentDisabledTests(MultiAgentTestCase):
    """CI runs with Multi-Agent disabled; nothing may contact the server."""
    enabled = False

    def test_status_start_get_and_review_report_disabled_without_contact(self):
        self.respond()
        status = self.client.get("/api/multi-agent/status").get_json()
        self.assertEqual((status["enabled"], status["reachable"]), (False, False))
        self.assertEqual(self.start().status_code, 503)
        self.assertEqual(self.client.get(f"/api/multi-agent/workflows/{WORKFLOW_ID}").status_code, 503)
        self.assertEqual(self.review({"decision": "approve"}).status_code, 503)
        self.assertEqual(self.calls, [])


class MultiAgentStartTests(MultiAgentTestCase):
    def test_start_sends_pharmacy_task_and_backend_evidence(self):
        self.respond((201, WORKFLOW))
        response = self.start()
        self.assertEqual(response.status_code, 201)
        method, path, payload = self.calls[0]
        self.assertEqual((method, path), ("POST", "/workflows"))
        self.assertEqual(payload["task"], "Pharmacy reorder decision: Paracetamol 500mg")
        self.assertEqual(payload["evidence"], build_evidence(CANDIDATE))
        body = response.get_json()
        self.assertEqual(body["workflow"]["status"], "awaiting_human_review")
        self.assertEqual(body["workflow"]["medicine"], "Paracetamol 500mg")
        self.assertEqual(body["evidence_summary"]["suggested_quantity"], 180)
        self.assertNotIn("raw", str(body["workflow"]["planner"]))  # internal model attempts are not forwarded

    def test_bad_requests_and_ineligible_medicines_never_reach_the_server(self):
        self.respond()
        for body in ([], {"medicine_id": "1"}, {"medicine_id": True}, {"medicine_id": 1, "task": "x"}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/multi-agent/reorder-workflows", json=body).status_code, 400)
        self.assertEqual(self.start({"medicine_id": 99}).status_code, 409)
        self.assertEqual(self.calls, [])

    def test_failures_map_to_safe_statuses(self):
        cases = ((urllib.error.URLError("refused"), 502, "unavailable"),
                 (socket.timeout(), 504, "timeout"),
                 ((503, {"error": {"code": "workflow_execution_failed", "message": "reviewer invalid"}}),
                  502, "workflow_failed"),
                 ((201, {"workflow_id": "not-a-uuid"}), 502, "unavailable"))
        for failure, status, outcome in cases:
            with self.subTest(outcome=outcome):
                self.respond(failure)
                response = self.start()
                self.assertEqual((response.status_code, response.get_json()["outcome"]), (status, outcome))


class MultiAgentScopeTests(MultiAgentTestCase):
    def test_get_returns_pharmacy_workflow(self):
        self.respond((200, WORKFLOW))
        response = self.client.get(f"/api/multi-agent/workflows/{WORKFLOW_ID}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[0][:2], ("GET", f"/workflows/{WORKFLOW_ID}"))

    def test_other_features_workflows_are_refused(self):
        self.respond((200, OTHER_FEATURE))
        response = self.client.get(f"/api/multi-agent/workflows/{WORKFLOW_ID}")
        self.assertEqual((response.status_code, response.get_json()["outcome"]), (403, "forbidden"))
        self.assertNotIn("staffing", response.get_data(as_text=True))

    def test_malformed_ids_are_not_forwarded(self):
        self.respond()
        for workflow_id in ("123", "../workflows", WORKFLOW_ID.upper() + "x"):
            with self.subTest(workflow_id=workflow_id):
                self.assertEqual(self.client.get(f"/api/multi-agent/workflows/{workflow_id}").status_code, 404)
        self.assertEqual(self.calls, [])


class MultiAgentReviewTests(MultiAgentTestCase):
    def test_manager_review_is_forwarded_after_scope_check(self):
        self.respond((200, WORKFLOW), (200, REVIEWED))
        response = self.review({"decision": "approve", "comment": " Order it. "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call[:2] for call in self.calls],
                         [("GET", f"/workflows/{WORKFLOW_ID}"), ("POST", f"/workflows/{WORKFLOW_ID}/review")])
        self.assertEqual(self.calls[1][2], {"decision": "approve", "comment": "Order it."})
        self.assertEqual(response.get_json()["workflow"]["status"], "completed")

    def test_pharmacist_cannot_review(self):
        self.respond()
        response = self.review({"decision": "approve"}, headers={"X-HOMS-Role": "Pharmacist"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_review_validation(self):
        self.respond()
        for body in ({"decision": "maybe"}, {"decision": "reject"}, {"decision": "correct", "comment": " "},
                     {"decision": "approve", "comment": "x" * 1001}, {"decision": "approve", "extra": 1}):
            with self.subTest(body=str(body)[:40]):
                self.assertEqual(self.review(body).status_code, 400)
        self.assertEqual(self.calls, [])

    def test_review_of_other_feature_or_finished_workflow_is_refused(self):
        self.respond((200, OTHER_FEATURE))
        self.assertEqual(self.review({"decision": "approve"}).status_code, 403)
        self.assertEqual(len(self.calls), 1)  # the review itself was never sent
        self.respond((200, REVIEWED), (409, {"error": {"code": "invalid_workflow_state", "message": "done"}}))
        self.assertEqual(self.review({"decision": "approve"}).status_code, 409)


class ReorderEvidenceTests(unittest.TestCase):
    def test_numbers_come_from_the_backend_candidate(self):
        evidence = build_evidence(CANDIDATE)
        # usable stock 15 + 30 on order = 45; the coverage target closes the suggested 180 gap
        self.assertEqual(evidence["objective"], {"metric": "usable_stock_units", "current": 45,
                                                 "target": 225, "unit": "units"})
        self.assertEqual([option["id"] for option in evidence["options"]],
                         ["order_suggested", "order_half", "defer"])
        self.assertEqual([option["outcomes"]["metric_delta"] for option in evidence["options"]], [180, 90, 0])
        self.assertEqual(evidence["options"][0]["label"], "Order the suggested 180 units ($21.60)")
        risks = " ".join(evidence["options"][0]["risk_indicators"])
        self.assertIn("5 units expire within the supplier lead time", risks)
        self.assertIn("30 units are already on open purchase orders", risks)
        self.assertFalse(evidence["options"][2]["attributes"]["quantity_positive"])
        self.assertIn("approval", [constraint["type"] for constraint in evidence["constraints"]])

    def test_large_orders_are_flagged_and_unused_medicines_add_an_unknown(self):
        evidence = build_evidence({**CANDIDATE, "suggested_quantity": 1, "unit_price": 750.0,
                                   "daily_usage_rate": 0})
        self.assertIn("above the $500.00 per-cycle cap", evidence["options"][0]["risk_indicators"][-1])
        self.assertEqual([option["id"] for option in evidence["options"]], ["order_suggested", "defer"])
        self.assertEqual(len(evidence["unknowns"]), 2)


if __name__ == "__main__":
    unittest.main()
