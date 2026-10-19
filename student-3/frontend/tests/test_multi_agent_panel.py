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
STARTED = {"ok": True, "outcome": "ok", "workflow": WORKFLOW,
           "evidence_summary": {"medicine_name": "Paracetamol 500mg", "supplier_name": "MedSupply Australia",
                                "available_quantity": 20, "near_expiry_quantity": 0, "open_order_quantity": 0,
                                "daily_usage_rate": 4.2, "lead_time_days": 5, "suggested_quantity": 180,
                                "unit_price": 0.12}}
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

    def test_errors_and_missing_input_are_explained(self):
        self.assertIn('Choose a medicine first', self.client.post('/multi-agent/start').get_data(as_text=True))
        self.assertIn('Choose a decision first', self.client.post(
            f'/multi-agent/workflows/{WORKFLOW_ID}/review', data={'decision': 'maybe'}).get_data(as_text=True))
        with patch.object(api_client, 'multi_agent_start',
                          side_effect=api_client.BackendError('Multi-Agent mode is disabled', 503)):
            html = self.client.post('/multi-agent/start', data={'medicine_id': '1'}).get_data(as_text=True)
        self.assertIn('Multi-Agent mode is disabled', html)
        self.assertIn('No inventory data or purchase order was changed', html)


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


if __name__ == "__main__":
    unittest.main()
