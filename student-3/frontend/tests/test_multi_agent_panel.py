"""Rendering checks for the dashboard's multi-agent reorder review panel."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import api_client

WORKFLOW_ID = "a42cc1a0-917e-460e-89c0-2426772ef65a"


def stage(output, model="llama3.1:8b", fallback=False):
    return {"output": output, "provider": "live_ollama", "model": model, "fallback_used": fallback,
            "started_at": "2026-10-19T01:00:00Z", "completed_at": "2026-10-19T01:00:20Z"}


WORKFLOW = {
    "workflow_id": WORKFLOW_ID, "status": "awaiting_human_review",
    "task": "Pharmacy reorder decision: Paracetamol 500mg", "medicine": "Paracetamol 500mg",
    "planner": stage({"goal": "Decide how much Paracetamol 500mg to reorder",
                      "planned_steps": ["Compare usable stock with the coverage target"]}),
    "worker": stage({"status": "proposal_ready",
                     "evaluated_options": [
                         {"option_id": "order_suggested", "label": "Order the suggested 180 units ($21.60)",
                          "eligible": True, "risk_indicators": []},
                         {"option_id": "defer", "label": "Do not order now", "eligible": False,
                          "risk_indicators": ["Stock stays at 20 units, below the coverage target of 200."]}],
                     "proposed_solution": {"selected_option": "Order the suggested 180 units ($21.60)",
                                           "reasoning": ["It closes the whole gap."]}}, model="llama3.2:3b"),
    "reviewer": stage({"review_status": "validated_with_risks", "risks": ["Demand may rise."],
                       "recommendation": "Order the suggested 180 units ($21.60)"}),
    "human_review": None, "final_result": None,
}
SUMMARY = {"medicine_name": "Paracetamol 500mg", "supplier_name": "MedSupply Australia",
           "available_quantity": 20, "near_expiry_quantity": 0, "open_order_quantity": 0,
           "daily_usage_rate": 4.2, "lead_time_days": 5, "suggested_quantity": 180, "unit_price": 0.12}
STARTED = {"ok": True, "outcome": "ok", "workflow": {**WORKFLOW, "evidence_summary": SUMMARY}}
RUNNING = {"ok": True, "outcome": "ok", "workflow": {**WORKFLOW, "status": "in_progress", "planner": WORKFLOW["planner"],
                                                     "worker": None, "reviewer": None, "evidence_summary": SUMMARY}}
REVIEWED = {"ok": True, "outcome": "ok", "workflow": {
    **WORKFLOW, "status": "corrected",
    "human_review": {"decision": "correct", "comments": "Order 120 units instead.",
                     "timestamp": "2026-10-19T01:02:30.123Z"}}}


class MultiAgentPanelTests(unittest.TestCase):
    role = "Pharmacy Manager"

    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': self.role, 'staff_id': 2, 'name': 'Demo person'}

    def test_dashboard_lazy_loads_the_panel_without_calling_the_server(self):
        summary = {"counts": {}, "low_stock": [], "expiring_soon": [], "recent_movements": [], "agent": None}
        with patch.object(api_client, 'dashboard_summary', return_value=summary), \
                patch.object(api_client, 'multi_agent_status', side_effect=AssertionError('eager call')), \
                patch.object(api_client, 'multi_agent_start', side_effect=AssertionError('eager call')):
            html = self.client.get('/').get_data(as_text=True)
        self.assertIn('Reorder decision review', html)
        self.assertIn('hx-get="/multi-agent/status"', html)
        self.assertIn('hx-get="/multi-agent/form"', html)

    def test_status_badge_variants(self):
        cases = [({"enabled": True, "reachable": True, "server_url": "http://x:8200"}, 'Multi-Agent connected'),
                 ({"enabled": False, "reachable": False, "server_url": "http://x:8200"}, 'Multi-Agent disabled'),
                 ({"enabled": True, "reachable": False, "server_url": "http://x:8200"},
                  'Multi-Agent server unreachable')]
        for status, text in cases:
            with self.subTest(text=text), patch.object(api_client, 'multi_agent_status', return_value=status):
                html = self.client.get('/multi-agent/status').get_data(as_text=True)
                self.assertIn(text, html)
                self.assertNotIn('http://x:8200', html)

    def test_form_lists_low_stock_medicines(self):
        candidates = [{"medicine_id": 1, "medicine_name": "Paracetamol 500mg", "supplier_name": "MedSupply",
                       "available_quantity": 20, "suggested_quantity": 180}]
        with patch.object(api_client, 'multi_agent_candidates', return_value=candidates):
            html = self.client.get('/multi-agent/form').get_data(as_text=True)
        self.assertIn('<option value="1">Paracetamol 500mg — 20 in stock, suggested order 180</option>', html)
        self.assertIn('Run multi-agent review', html)
        with patch.object(api_client, 'multi_agent_candidates', return_value=[]):
            self.assertIn('No medicine currently needs', self.client.get('/multi-agent/form').get_data(as_text=True))

    def test_start_shows_all_three_agents_and_the_manager_decision_form(self):
        with patch.object(api_client, 'multi_agent_start', return_value=STARTED) as start:
            html = self.client.post('/multi-agent/start', data={'medicine_id': '1'}).get_data(as_text=True)
        start.assert_called_once_with(1)
        for text in ('Awaiting human review', '1. Planner', 'Decide how much Paracetamol 500mg to reorder',
                     '2. Worker', 'Order the suggested 180 units ($21.60)', 'llama3.2:3b · live',
                     '3. Reviewer', 'Validated with risks', 'Demand may rise.', '4. Human review',
                     'value="partially_accept"', 'Record decision', '20 in stock'):
            self.assertIn(text, html)

    def test_a_running_workflow_polls_until_the_agents_finish(self):
        with patch.object(api_client, 'multi_agent_start', return_value=RUNNING):
            html = self.client.post('/multi-agent/start', data={'medicine_id': '1'}).get_data(as_text=True)
        self.assertIn(f'hx-get="/multi-agent/workflows/{WORKFLOW_ID}" hx-trigger="load delay:3s"', html)
        for text in ('Agents working…', 'Decide how much Paracetamol 500mg to reorder', 'Waiting for the Planner…',
                     'Waiting for the Worker…', 'Available once the Reviewer has finished', '20 in stock'):
            self.assertIn(text, html)
        self.assertNotIn('Record decision', html)
        with patch.object(api_client, 'multi_agent_workflow', return_value=STARTED) as poll:
            html = self.client.get(f'/multi-agent/workflows/{WORKFLOW_ID}').get_data(as_text=True)
        poll.assert_called_once_with(WORKFLOW_ID)
        self.assertNotIn('hx-trigger="load delay:3s"', html)  # finished: polling stops
        self.assertIn('Validated with risks', html)

    def test_fallback_stages_are_labelled_as_rule_based(self):
        fallback = {**WORKFLOW, "worker": {**WORKFLOW["worker"], "model": None, "fallback_used": True}}
        with patch.object(api_client, 'multi_agent_workflow',
                          return_value={"ok": True, "outcome": "ok", "workflow": fallback}):
            html = self.client.get(f'/multi-agent/workflows/{WORKFLOW_ID}').get_data(as_text=True)
        self.assertIn('Rule-based fallback', html)
        self.assertIn('llama3.1:8b · live', html)

    def test_review_is_sent_with_the_manager_role(self):
        with patch.object(api_client, 'multi_agent_review', return_value=REVIEWED) as review:
            html = self.client.post(f'/multi-agent/workflows/{WORKFLOW_ID}/review',
                                    data={'decision': 'correct', 'comment': 'Order 120 units instead.'}
                                    ).get_data(as_text=True)
        review.assert_called_once_with(WORKFLOW_ID, 'correct', 'Order 120 units instead.', 'Pharmacy Manager')
        self.assertIn('Corrected', html)
        self.assertIn('Order 120 units instead.', html)
        self.assertIn('2026-10-19 01:02 UTC', html)
        self.assertNotIn('Record decision', html)

    def test_a_missing_comment_keeps_the_form_and_explains_the_rule(self):
        with patch.object(api_client, 'multi_agent_workflow', return_value=STARTED), \
                patch.object(api_client, 'multi_agent_review', side_effect=AssertionError('must not be sent')):
            html = self.client.post(f'/multi-agent/workflows/{WORKFLOW_ID}/review',
                                    data={'decision': 'correct', 'comment': '  '}).get_data(as_text=True)
        self.assertIn('it is required for Correct, Partially accept and Reject', html)
        self.assertIn('3. Reviewer', html)  # the agents' results stay on screen
        self.assertIn('value="correct" required checked', html)
        self.assertIn('Record decision', html)

    def test_a_backend_validation_error_keeps_the_form(self):
        with patch.object(api_client, 'multi_agent_workflow', return_value=STARTED), \
                patch.object(api_client, 'multi_agent_review',
                             side_effect=api_client.BackendError('comment must be text of at most 1000 characters', 400)):
            html = self.client.post(f'/multi-agent/workflows/{WORKFLOW_ID}/review',
                                    data={'decision': 'reject', 'comment': 'Too much'}).get_data(as_text=True)
        self.assertIn('comment must be text of at most 1000 characters', html)
        self.assertIn('value="Too much"', html)
        self.assertIn('Record decision', html)

    def test_errors_and_missing_input_are_explained(self):
        self.assertIn('Choose a medicine first', self.client.post('/multi-agent/start').get_data(as_text=True))
        with patch.object(api_client, 'multi_agent_workflow', return_value=STARTED):
            html = self.client.post(f'/multi-agent/workflows/{WORKFLOW_ID}/review',
                                    data={'decision': 'maybe'}).get_data(as_text=True)
        self.assertIn('Choose a decision first', html)
        self.assertIn('Record decision', html)
        with patch.object(api_client, 'multi_agent_start',
                          side_effect=api_client.BackendError('Multi-Agent mode is disabled', 503)):
            html = self.client.post('/multi-agent/start', data={'medicine_id': '1'}).get_data(as_text=True)
        self.assertIn('Multi-Agent mode is disabled', html)
        self.assertIn('No inventory data or purchase order was changed', html)


CONTEXT = {"mcp_order_alerts": {"source": "MCP", "pharmacy_overdue_deliveries": 22, "pharmacy_pending_approval": 16,
                                "listed_orders_for_this_medicine": [{"po_id": 7, "status": "approved",
                                                                     "outstanding": 40, "days_overdue": 21}],
                                "note": "n"},
           "rag_reorder_policy": {"source": "RAG", "answers": [
               {"question": "Who must approve a purchase order?", "confidence": "high",
                "answer": "Only a `Pharmacy Manager` can approve [S1].",
                "citations": ["Reordering and Purchase Orders > Human approval"]}]}}


class MultiAgentContextAndHistoryTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacy Manager', 'staff_id': 2, 'name': 'Demo manager'}

    def test_result_shows_the_mcp_and_rag_context_given_to_the_agents(self):
        body = {"ok": True, "outcome": "ok", "workflow": {**WORKFLOW, "evidence_summary": {**SUMMARY, **CONTEXT}}}
        with patch.object(api_client, 'multi_agent_workflow', return_value=body):
            html = self.client.get(f'/multi-agent/workflows/{WORKFLOW_ID}').get_data(as_text=True)
        for text in ('Context given to the agents', '22 overdue deliveries', '16 orders awaiting approval',
                     '#7 approved, 21 days overdue', 'RAG: Who must approve a purchase order?', 'High confidence',
                     'Only a Pharmacy Manager can approve',
                     'Reordering and Purchase Orders › Human approval'):
            self.assertIn(text, html)

    def test_missing_context_is_stated(self):
        body = {"ok": True, "outcome": "ok", "workflow": {**WORKFLOW, "evidence_summary": {
            **SUMMARY, "mcp_order_alerts": None, "rag_reorder_policy": CONTEXT["rag_reorder_policy"]}}}
        with patch.object(api_client, 'multi_agent_workflow', return_value=body):
            html = self.client.get(f'/multi-agent/workflows/{WORKFLOW_ID}').get_data(as_text=True)
        self.assertIn('MCP purchase-order alerts were not available', html)

    def test_finished_workflows_refresh_the_history_but_running_ones_do_not(self):
        with patch.object(api_client, 'multi_agent_workflow', return_value=STARTED):
            response = self.client.get(f'/multi-agent/workflows/{WORKFLOW_ID}')
        self.assertEqual(response.headers.get('HX-Trigger'), 'multiAgentChanged')
        with patch.object(api_client, 'multi_agent_start', return_value=RUNNING):
            response = self.client.post('/multi-agent/start', data={'medicine_id': '1'})
        self.assertIsNone(response.headers.get('HX-Trigger'))

    def test_panel_loads_the_history_and_refreshes_it(self):
        summary = {"counts": {}, "low_stock": [], "expiring_soon": [], "recent_movements": [], "agent": None}
        with patch.object(api_client, 'dashboard_summary', return_value=summary):
            html = self.client.get('/').get_data(as_text=True)
        self.assertIn('hx-get="/multi-agent/history" hx-trigger="load, multiAgentChanged from:body"', html)

    def test_history_table_lists_reviews_with_open_buttons(self):
        reviews = [{"workflow_id": WORKFLOW_ID, "medicine": "Paracetamol 500mg", "status": "corrected",
                    "started_at": "2026-10-22T01:02:30Z", "decision": "correct", "comment": "Order 120 units."},
                   {"workflow_id": "11111111-2222-3333-4444-555555555555", "medicine": "Ibuprofen 400mg",
                    "status": "awaiting_human_review", "started_at": "2026-10-22T02:00:00Z",
                    "decision": None, "comment": None}]
        with patch.object(api_client, 'multi_agent_history', return_value=reviews):
            html = self.client.get('/multi-agent/history').get_data(as_text=True)
        for text in ('Paracetamol 500mg', '2026-10-22 01:02', 'Corrected', 'Order 120 units.',
                     'Ibuprofen 400mg', 'Awaiting human review',
                     f'hx-get="/multi-agent/workflows/{WORKFLOW_ID}" hx-target="#multi-agent-result"'):
            self.assertIn(text, html)
        with patch.object(api_client, 'multi_agent_history', return_value=[]):
            self.assertIn('No reorder reviews yet', self.client.get('/multi-agent/history').get_data(as_text=True))
        with patch.object(api_client, 'multi_agent_history',
                          side_effect=api_client.BackendError('Multi-Agent mode is disabled', 503)):
            self.assertIn('Multi-Agent mode is disabled', self.client.get('/multi-agent/history').get_data(as_text=True))


class PharmacistMultiAgentTests(MultiAgentPanelTests):
    role = "Pharmacist"

    def test_start_shows_all_three_agents_and_the_manager_decision_form(self):
        with patch.object(api_client, 'multi_agent_start', return_value=STARTED):
            html = self.client.post('/multi-agent/start', data={'medicine_id': '1'}).get_data(as_text=True)
        self.assertIn('3. Reviewer', html)
        self.assertIn("Waiting for a Pharmacy Manager's decision", html)
        self.assertNotIn('Record decision', html)

    def test_review_is_sent_with_the_manager_role(self):
        with patch.object(api_client, 'multi_agent_review', side_effect=AssertionError('not allowed')):
            response = self.client.post(f'/multi-agent/workflows/{WORKFLOW_ID}/review',
                                        data={'decision': 'approve'})
        self.assertEqual(response.status_code, 403)
        self.assertIn('Pharmacy Manager role required', response.get_data(as_text=True))

    def test_errors_and_missing_input_are_explained(self):
        self.assertIn('Choose a medicine first', self.client.post('/multi-agent/start').get_data(as_text=True))

    def test_a_missing_comment_keeps_the_form_and_explains_the_rule(self):
        response = self.client.post(f'/multi-agent/workflows/{WORKFLOW_ID}/review', data={'decision': 'correct'})
        self.assertEqual(response.status_code, 403)

    def test_a_backend_validation_error_keeps_the_form(self):
        response = self.client.post(f'/multi-agent/workflows/{WORKFLOW_ID}/review',
                                    data={'decision': 'reject', 'comment': 'x'})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
