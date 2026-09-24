"""Rendering checks for the dashboard's shared MCP tools panel."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import api_client

STOCK_ALERTS = {
    "ok": True, "outcome": "ok", "tool": "homs_pharmacy_stock_alerts",
    "arguments": {"alert_type": "all"}, "duration_ms": 42,
    "result": {
        "schema_version": "1.0", "ok": True, "tool": "homs_pharmacy_stock_alerts", "error": None,
        "data": {
            "alert_type": "all",
            "counts": {"active_medicines": 132, "low_stock": 26, "expiring_within_30_days": 28,
                       "expiring_within_7_days": 7, "expired_batches": 26, "pending_approvals": 10},
            "low_stock": [{"name": "Paracetamol 500mg", "stock_quantity": 20, "reorder_level": 200,
                           "supplier_name": "MedSupply Australia"}],
            "expiring_soon": [{"medicine_name": "Salbutamol <inhaler>", "batch_number": "PHM-26026",
                               "expiry_date": "2026-09-24", "quantity_remaining": 200, "days_until_expiry": 1}],
            "source": "student-3-pharmacy-api",
        },
    },
}


class MCPPanelTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacy Staff', 'staff_id': 2, 'name': 'Demo staff'}

    def test_dashboard_lazy_loads_mcp_status_without_calling_mcp(self):
        summary = {"counts": {}, "low_stock": [], "expiring_soon": [], "recent_movements": [], "agent": None}
        with patch.object(api_client, 'dashboard_summary', return_value=summary), \
                patch.object(api_client, 'agent_status', return_value=None), \
                patch.object(api_client, 'mcp_status', side_effect=AssertionError('eager MCP call')):
            html = self.client.get('/').get_data(as_text=True)
        self.assertIn('Shared MCP tools', html)
        self.assertIn('hx-get="/mcp/status"', html)
        self.assertIn('value="homs_pharmacy_stock_alerts"', html)

    def test_status_badge_variants(self):
        cases = [
            ({"enabled": True, "reachable": True, "server_url": "http://127.0.0.1:8000/mcp",
              "tools": [{"name": "homs_echo"}, {"name": "homs_pharmacy_stock_alerts"}]}, 'MCP connected · 2 tools'),
            ({"enabled": False, "reachable": False, "server_url": "x", "tools": []}, 'MCP disabled'),
            ({"enabled": True, "reachable": False, "server_url": "x", "tools": []}, 'MCP server unreachable'),
        ]
        for status, text in cases:
            with self.subTest(text=text), patch.object(api_client, 'mcp_status', return_value=status):
                self.assertIn(text, self.client.get('/mcp/status').get_data(as_text=True))
        with patch.object(api_client, 'mcp_status', side_effect=api_client.BackendError('down')):
            self.assertIn('Backend unavailable', self.client.get('/mcp/status').get_data(as_text=True))

    def test_stock_alerts_forwards_only_alert_type_and_renders_result(self):
        with patch.object(api_client, 'mcp_call', return_value=STOCK_ALERTS) as call:
            html = self.client.post('/mcp/call', data={
                'tool': 'homs_pharmacy_stock_alerts', 'alert_type': 'low_stock', 'message': 'ignored'}).get_data(as_text=True)
        call.assert_called_once_with('homs_pharmacy_stock_alerts', {'alert_type': 'low_stock'})
        for text in ('Valid tool result', 'Paracetamol 500mg', 'MedSupply Australia', '1 of 26 shown',
                     'PHM-26026', 'student-3-pharmacy-api', 'Structured result (JSON)', '42 ms'):
            self.assertIn(text, html)
        self.assertIn('Salbutamol &lt;inhaler&gt;', html)
        self.assertNotIn('Salbutamol <inhaler>', html)

    def test_echo_forwards_only_message(self):
        response = {"ok": True, "outcome": "ok", "tool": "homs_echo", "arguments": {"message": "hi"},
                    "duration_ms": 3, "result": {"ok": True, "data": {"message": "hi"}, "error": None}}
        with patch.object(api_client, 'mcp_call', return_value=response) as call:
            html = self.client.post('/mcp/call', data={'tool': 'homs_echo', 'message': 'hi',
                                                       'alert_type': 'all'}).get_data(as_text=True)
        call.assert_called_once_with('homs_echo', {'message': 'hi'})
        self.assertIn('echoed: <strong>hi</strong>', html)

    def test_tool_error_is_shown_as_structured_error(self):
        response = {"ok": False, "outcome": "tool_error", "tool": "homs_pharmacy_stock_alerts",
                    "arguments": {"alert_type": "bogus"}, "duration_ms": 2,
                    "result": {"ok": False, "data": None,
                               "error": {"code": "validation_error", "message": "bad alert type", "details": {}}}}
        with patch.object(api_client, 'mcp_call', return_value=response):
            html = self.client.post('/mcp/call', data={'tool': 'homs_pharmacy_stock_alerts',
                                                       'alert_type': 'bogus'}).get_data(as_text=True)
        self.assertIn('Tool returned an error', html)
        self.assertIn('validation_error', html)
        self.assertIn('bad alert type', html)

    def test_backend_failure_renders_escaped_error(self):
        with patch.object(api_client, 'mcp_call', side_effect=api_client.BackendError('<MCP mode is disabled>', 503)):
            html = self.client.post('/mcp/call', data={'tool': 'homs_echo', 'message': 'x'}).get_data(as_text=True)
        self.assertIn('MCP tool call failed', html)
        self.assertIn('&lt;MCP mode is disabled&gt;', html)
        self.assertIn('No inventory data was changed', html)

    def test_unknown_tool_is_not_forwarded(self):
        with patch.object(api_client, 'mcp_call', side_effect=AssertionError('forwarded')):
            html = self.client.post('/mcp/call', data={'tool': 'homs_ward_occupancy_status'}).get_data(as_text=True)
        self.assertIn('Unknown MCP tool', html)


if __name__ == '__main__':
    unittest.main()
