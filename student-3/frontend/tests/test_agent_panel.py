"""Rendering checks for the agent status and human review controls."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import api_client


class AgentPanelTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacy Manager', 'staff_id': 1, 'name': 'Demo manager'}

    def test_panel_renders_requested_status_fields(self):
        status = dict(enabled=True, interval_seconds=60, max_proposals=3, budget_cap='500.00',
                      last_cycle_time='2026-09-06T06:00:00+00:00', running=False,
                      proposals_created=2, observe_rejected=4, last_error=None)
        with patch.object(api_client, 'agent_status', return_value=status):
            html = self.client.get('/purchase-orders/agent').get_data(as_text=True)
        for value in ('Enabled', 'Every 60 seconds', '2026-09-06T06:00:00+00:00',
                      'Created last cycle: <strong>2', 'Rejected by Observe: <strong>4'):
            self.assertIn(value, html)
        status['enabled'] = False
        with patch.object(api_client, 'agent_status', return_value=status):
            self.assertIn('Disabled', self.client.get('/purchase-orders/agent').get_data(as_text=True))

    def test_backend_failure_renders_clear_status_and_escapes_error(self):
        with patch.object(api_client, 'agent_status', side_effect=api_client.BackendError('<offline>')):
            response = self.client.get('/purchase-orders/agent')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Agent status unavailable: &lt;offline&gt;', response.get_data(as_text=True))

    def test_edit_requires_manager_and_proxies_reason(self):
        with patch.object(api_client, 'save_purchase_order') as save:
            response = self.client.post('/purchase-orders/71/edit', data={'quantity_ordered': '40', 'decision_reason': 'Reduce waste'})
            self.assertEqual(response.status_code, 302)
            save.assert_called_once_with({'quantity_ordered':'40', 'decision_reason':'Reduce waste'}, 'Pharmacy Manager', 71)
        with self.client.session_transaction() as session:
            session['demo_identity']['role'] = 'Pharmacist'
            session.modified = True
        with patch.object(api_client, 'save_purchase_order') as save:
            self.assertEqual(self.client.post('/purchase-orders/71/edit').status_code, 403)
            save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
