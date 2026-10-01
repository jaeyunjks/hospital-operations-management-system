"""Clickable stock-alert tiles in the MCP result and their detail panels."""
import copy
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import api_client
from test_mcp_panel import STOCK_ALERTS

TILES = ('active', 'low', 'exp30', 'exp7', 'expired', 'pending')


def panel(html, key):
    """Return the HTML of one detail panel."""
    match = re.search(rf'<div class="mcp-detail" id="mcp-panel-{key}".*?(?=<div class="mcp-detail" id="mcp-panel-|</article>)',
                      html, re.S)
    assert match, f'panel {key} missing'
    return match.group(0)


class StockAlertTileTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacist', 'staff_id': 3, 'name': 'Demo pharmacist'}

    def render(self, result):
        with patch.object(api_client, 'mcp_call', return_value=result):
            return self.client.post('/mcp/call', data={'tool': 'homs_pharmacy_stock_alerts',
                                                       'alert_type': 'all'}).get_data(as_text=True)

    def alerts_with_two_expiring_batches(self):
        result = copy.deepcopy(STOCK_ALERTS)
        result['result']['data']['expiring_soon'].append(
            {"medicine_name": "Morphine 10mg/mL", "batch_number": "PHM-26999", "expiry_date": "2026-10-13",
             "quantity_remaining": 30, "days_until_expiry": 20})
        return result

    def test_six_clickable_tiles_with_hidden_panels(self):
        html = self.render(STOCK_ALERTS)
        self.assertEqual(re.findall(r'data-mcp-tile="(\w+)"', html), list(TILES))
        self.assertEqual(html.count('aria-pressed="false"'), 6)
        for key in TILES:
            self.assertIn(' hidden>', panel(html, key)[:120])
        self.assertIn('Click a figure to see its details.', html)
        self.assertIn('<span class="mcp-tile__value">26</span>', html)

    def test_backend_tiles_load_details_once(self):
        html = self.render(STOCK_ALERTS)
        for key, kind in (('active', 'active-medicines'), ('expired', 'expired-batches'),
                          ('pending', 'pending-approvals')):
            self.assertRegex(html, rf'data-mcp-tile="{key}"[^>]*hx-get="/mcp/details/{kind}"[^>]*'
                                   rf'hx-target="#mcp-panel-{key}"[^>]*hx-trigger="click once"')
        for key in ('low', 'exp30', 'exp7'):
            self.assertNotRegex(html, rf'data-mcp-tile="{key}"[^>]*hx-get')

    def test_each_mcp_panel_shows_only_its_own_rows(self):
        html = self.render(self.alerts_with_two_expiring_batches())
        low, exp30, exp7 = panel(html, 'low'), panel(html, 'exp30'), panel(html, 'exp7')
        self.assertIn('Paracetamol 500mg', low)
        self.assertNotIn('PHM-26026', low)
        self.assertIn('PHM-26026', exp30)
        self.assertIn('PHM-26999', exp30)
        self.assertIn('PHM-26026', exp7)
        self.assertNotIn('PHM-26999', exp7)
        self.assertIn('(1 of 7 shown)', exp7)

    def test_lists_not_requested_are_explained(self):
        result = copy.deepcopy(STOCK_ALERTS)
        result['result']['data']['low_stock'] = None
        html = self.render(result)
        self.assertIn('Not included in this request', panel(html, 'low'))
        self.assertNotIn('Not included in this request', panel(html, 'exp30'))


    def test_expiring_soon_opens_on_7_day_then_8_to_30_day_lists(self):
        result = self.alerts_with_two_expiring_batches()
        result['arguments'] = {'alert_type': 'expiring_soon'}
        result['result']['data']['alert_type'] = 'expiring_soon'
        result['result']['data']['low_stock'] = None
        html = self.render(result)
        default = panel(html, 'default')
        self.assertNotIn(' hidden', default[:160])
        self.assertIn('data-mcp-default', default)
        seven, later = default.index('Expiring within 7 days'), default.index('Expiring in 8–30 days')
        self.assertLess(seven, later)
        self.assertIn('PHM-26026', default[seven:later])
        self.assertNotIn('PHM-26999', default[seven:later])
        self.assertIn('PHM-26999', default[later:])
        self.assertNotIn('PHM-26026', default[later:])
        self.assertIn('(1 of 21 shown)', default)  # 28 within 30 days minus 7 within 7 days
        self.assertRegex(html, r'<p class="table__muted" data-mcp-hint hidden>')

    def test_other_alert_types_have_no_default_view(self):
        html = self.render(STOCK_ALERTS)
        self.assertNotIn('data-mcp-default', html)
        self.assertRegex(html, r'<p class="table__muted" data-mcp-hint>')

class TileDetailRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacist', 'staff_id': 3, 'name': 'Demo pharmacist'}

    def test_active_medicines(self):
        payload = {"medicines": [{"name": "Paracetamol 500mg", "category": "Analgesic", "available_quantity": 20,
                                  "reorder_level": 200, "supplier_name": "MedSupply Australia"}],
                   "pagination": {"total_items": 132}}
        with patch.object(api_client, 'list_medicines', return_value=payload) as list_medicines:
            html = self.client.get('/mcp/details/active-medicines').get_data(as_text=True)
        self.assertEqual(list_medicines.call_args.kwargs['status'], 'active')
        self.assertEqual(list_medicines.call_args.kwargs['page_size'], '10')
        for text in ('Active medicines', '(1 of 132 shown)', 'Paracetamol 500mg', 'MedSupply Australia',
                     'not part of the MCP result', 'href="/medicines"'):
            self.assertIn(text, html)

    def test_expired_batches_show_days_expired(self):
        payload = {"batches": [{"medicine_name": "Ceftriaxone 1g", "batch_number": "PHM-1", "expiry_date": "2026-09-20",
                                "days_until_expiry": -11, "quantity_remaining": 45}],
                   "pagination": {"total_items": 25}}
        with patch.object(api_client, 'list_batches', return_value=payload) as list_batches:
            html = self.client.get('/mcp/details/expired-batches').get_data(as_text=True)
        self.assertEqual(list_batches.call_args.kwargs['expiry_status'], 'expired')
        for text in ('Expired batches', '(1 of 25 shown)', 'Ceftriaxone 1g', '<td class="table__num">11</td>',
                     'href="/batches?expiry_status=expired"'):
            self.assertIn(text, html)

    def test_pending_approvals(self):
        payload = {"purchase_orders": [{"medicine_name": "Dextrose 5%", "supplier_name": "Southern Clinical Supplies",
                                        "quantity_ordered": 120, "total_value": 54.5, "ai_generated": 1,
                                        "created_at": "2026-09-30T00:00:00"}],
                   "pagination": {"total_items": 16}}
        with patch.object(api_client, 'list_purchase_orders', return_value=payload) as list_orders:
            html = self.client.get('/mcp/details/pending-approvals').get_data(as_text=True)
        self.assertEqual(list_orders.call_args.kwargs['status'], 'pending_approval')
        for text in ('Pending approvals', '(1 of 16 shown)', 'Dextrose 5%', '$54.50', 'AI-suggested', '2026-09-30',
                     'href="/purchase-orders?status=pending_approval"'):
            self.assertIn(text, html)

    def test_unknown_kind_and_backend_failure(self):
        self.assertEqual(self.client.get('/mcp/details/patients').status_code, 404)
        with patch.object(api_client, 'list_batches', side_effect=api_client.BackendError('<down>')):
            html = self.client.get('/mcp/details/expired-batches').get_data(as_text=True)
        self.assertIn('&lt;down&gt;. No inventory data was changed.', html)


if __name__ == '__main__':
    unittest.main()
