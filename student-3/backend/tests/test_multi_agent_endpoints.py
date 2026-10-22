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
ACCEPTED = {**WORKFLOW, "status": "in_progress", "planner": None, "worker": None, "reviewer": None}
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
        self.assertEqual(self.client.get("/api/multi-agent/workflows").status_code, 503)
        self.assertEqual(self.calls, [])


class MultiAgentStartTests(MultiAgentTestCase):
    def test_start_sends_pharmacy_task_and_backend_evidence(self):
        self.respond((202, ACCEPTED))
        response = self.start()  # MCP and RAG are disabled here, so the evidence notes they were unavailable
        self.assertEqual(response.status_code, 202)  # accepted; the agents run in the background
        method, path, payload = self.calls[0]
        self.assertEqual((method, path), ("POST", "/workflows"))
        self.assertEqual(payload["task"], "Pharmacy reorder decision: Paracetamol 500mg")
        self.assertEqual(payload["evidence"], build_evidence(CANDIDATE))
        body = response.get_json()
        self.assertEqual(body["workflow"]["status"], "in_progress")
        self.assertEqual(body["workflow"]["medicine"], "Paracetamol 500mg")
        self.assertEqual(body["workflow"]["evidence_summary"]["suggested_quantity"], 180)

    def test_start_adds_mcp_and_rag_context(self):
        mcp = {"source": "MCP", "pharmacy_overdue_deliveries": 22, "pharmacy_pending_approval": 16,
               "listed_orders_for_this_medicine": [], "note": "n"}
        rag = {"source": "RAG", "answers": [{"question": "q", "answer": "a [S1]", "confidence": "high",
                                             "citations": ["T > S"]}]}
        self.respond((202, ACCEPTED))
        with patch.object(app, "mcp_order_context", return_value=mcp) as mcp_call, \
                patch.object(app, "rag_policy_context", return_value=rag):
            response = self.start()
        mcp_call.assert_called_once_with("Paracetamol 500mg")
        self.assertEqual(self.calls[0][2]["evidence"], build_evidence(CANDIDATE, mcp, rag))
        summary = response.get_json()["workflow"]["evidence_summary"]
        self.assertEqual((summary["mcp_order_alerts"], summary["rag_reorder_policy"]), (mcp, rag))

    def test_a_finished_synchronous_reply_is_also_accepted(self):
        self.respond((201, WORKFLOW))
        response = self.start()
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("raw", str(response.get_json()["workflow"]["planner"]))  # internal attempts are not forwarded

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
    def test_get_returns_pharmacy_workflow_with_its_evidence_summary(self):
        self.respond((200, {**WORKFLOW, "submitted_evidence": build_evidence(CANDIDATE)}))
        response = self.client.get(f"/api/multi-agent/workflows/{WORKFLOW_ID}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[0][:2], ("GET", f"/workflows/{WORKFLOW_ID}?include_evidence=true"))
        summary = response.get_json()["workflow"]["evidence_summary"]
        self.assertEqual((summary["medicine_name"], summary["available_quantity"], summary["near_expiry_quantity"],
                          summary["open_order_quantity"], summary["suggested_quantity"]),
                         ("Paracetamol 500mg", 20, 5, 30, 180))
        self.assertNotIn("submitted_evidence", response.get_json()["workflow"])

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


class MultiAgentHistoryTests(MultiAgentTestCase):
    def test_history_lists_only_pharmacy_reviews_with_their_decisions(self):
        other_id = "11111111-2222-3333-4444-555555555555"
        listing = {"count": 2, "workflows": [
            {"workflow_id": WORKFLOW_ID, "status": "completed", "started_at": "2026-10-19T01:00:00Z"},
            {"workflow_id": other_id, "status": "approved", "started_at": "2026-10-18T01:00:00Z"}]}
        self.respond((200, listing), (200, REVIEWED), (200, {**OTHER_FEATURE, "workflow_id": other_id}))
        response = self.client.get("/api/multi-agent/workflows?limit=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[0][:2],
                         ("GET", "/workflows?evidence_type=student3_reorder_decision&limit=5"))
        reviews = response.get_json()["reviews"]
        self.assertEqual(len(reviews), 1)  # the other feature's workflow is dropped
        self.assertEqual((reviews[0]["medicine"], reviews[0]["decision"], reviews[0]["comment"]),
                         ("Paracetamol 500mg", "approve", "Order it."))

    def test_history_validation_and_failures(self):
        self.respond()
        for query in ("?limit=0", "?limit=21", "?limit=x"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/api/multi-agent/workflows" + query).status_code, 400)
        self.respond(urllib.error.URLError("refused"))
        response = self.client.get("/api/multi-agent/workflows")
        self.assertEqual((response.status_code, response.get_json()["outcome"]), (502, "unavailable"))


class MultiAgentReviewTests(MultiAgentTestCase):
    def test_manager_review_is_forwarded_after_scope_check(self):
        self.respond((200, WORKFLOW), (200, REVIEWED))
        response = self.review({"decision": "approve", "comment": " Order it. "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([call[:2] for call in self.calls],
                         [("GET", f"/workflows/{WORKFLOW_ID}?include_evidence=true"),
                          ("POST", f"/workflows/{WORKFLOW_ID}/review")])
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
        self.assertIn("No stock was issued in the last 30 days", " ".join(evidence["unknowns"]))

    def test_mcp_and_rag_context_are_added_as_evidence(self):
        mcp = {"source": "MCP tool homs_pharmacy_order_alerts", "pharmacy_overdue_deliveries": 22,
               "pharmacy_pending_approval": 16,
               "listed_orders_for_this_medicine": [{"po_id": 7, "status": "approved", "outstanding": 40,
                                                    "expected_at": "2026-10-01", "days_overdue": 21}],
               "note": "MCP lists up to 10 orders per category; counts cover all orders."}
        rag = {"source": "RAG knowledge base (student-3)", "answers": [
            {"question": "q", "answer": "Only a Pharmacy Manager [S1].", "confidence": "high",
             "citations": ["Reordering and Purchase Orders > Human approval"]}]}
        evidence = build_evidence(CANDIDATE, mcp, rag)
        self.assertEqual(evidence["evidence_type"], "student3_reorder_decision")
        self.assertEqual((evidence["mcp_order_alerts"], evidence["rag_reorder_policy"]), (mcp, rag))
        self.assertIn("Purchase order #7 for this medicine is 21 days overdue (MCP)",
                      " ".join(evidence["options"][0]["risk_indicators"]))
        self.assertNotIn("MCP server were not available", " ".join(evidence["unknowns"]))
        bare = build_evidence(CANDIDATE)
        self.assertNotIn("mcp_order_alerts", bare)
        self.assertIn("Live purchase-order alerts from the shared MCP server were not available.", bare["unknowns"])
        self.assertIn("The reorder and approval rules from the shared RAG server were not available.", bare["unknowns"])


class ReorderContextTests(unittest.TestCase):
    """The MCP and RAG context gathered before a review starts."""

    def test_mcp_context_keeps_counts_and_this_medicines_orders(self):
        from services import mcp_client, reorder_context
        order = {"po_id": 7, "medicine_name": "Paracetamol 500mg", "status": "approved", "outstanding": 40,
                 "expected_at": "2026-10-01", "days_overdue": 21}
        other = {**order, "po_id": 8, "medicine_name": "Ibuprofen 400mg"}
        data = {"counts": {"overdue": 22, "pending_approval": 16}, "pending_approval": [], "approved": [order],
                "ordered": [], "overdue": [order, other]}
        reply = mcp_client.MCPResult(True, "ok", result={"ok": True, "data": data})
        with patch.object(mcp_client, "call_tool", return_value=reply) as call:
            context = reorder_context.mcp_order_context("Paracetamol 500mg")
        call.assert_called_once_with("homs_pharmacy_order_alerts", {"alert_type": "all"})
        self.assertEqual((context["pharmacy_overdue_deliveries"], context["pharmacy_pending_approval"]), (22, 16))
        self.assertEqual([o["po_id"] for o in context["listed_orders_for_this_medicine"]], [7])  # de-duplicated
        with patch.object(mcp_client, "call_tool", return_value=mcp_client.MCPResult(False, "disabled")):
            self.assertIsNone(reorder_context.mcp_order_context("Paracetamol 500mg"))

    def test_rag_context_keeps_only_answered_questions(self):
        from services import rag_client, reorder_context
        answered = rag_client.RAGResult(True, "answered", "q", answer="Only a Pharmacy Manager can approve [S1].",
                                        confidence="high", citations=[{"title": "Reordering and Purchase Orders",
                                                                       "section": "Human approval"}])
        refused = rag_client.RAGResult(True, "insufficient_context", "q")
        with patch.object(rag_client, "ask", side_effect=[refused, answered]) as ask:
            context = reorder_context.rag_policy_context()
        self.assertEqual([call.args[0] for call in ask.call_args_list], list(reorder_context.POLICY_QUESTIONS))
        self.assertEqual(len(context["answers"]), 1)
        self.assertEqual(context["answers"][0]["citations"], ["Reordering and Purchase Orders > Human approval"])
        with patch.object(rag_client, "ask", return_value=refused):
            self.assertIsNone(reorder_context.rag_policy_context())


if __name__ == "__main__":
    unittest.main()
