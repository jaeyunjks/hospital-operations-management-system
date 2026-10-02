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

    def test_low_stock_opens_on_the_low_stock_list(self):
        result = copy.deepcopy(STOCK_ALERTS)
        result['arguments'] = {'alert_type': 'low_stock'}
        result['result']['data']['alert_type'] = 'low_stock'
        result['result']['data']['expiring_soon'] = None
        html = self.render(result)
        default = panel(html, 'default')
        self.assertNotIn(' hidden', default[:160])
        self.assertIn('Low stock', default)
        self.assertIn('(1 of 26 shown)', default)
        self.assertIn('Paracetamol 500mg', default)
        self.assertNotIn('Expiring', default)
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


def order_row(po_id, status, *, ai=False, days_overdue=0):
    return {"po_id": po_id, "medicine_name": f"Medicine {po_id}", "supplier_name": "MedSupply Australia",
            "status": status, "quantity_ordered": 100, "outstanding": 100, "total_value": 250.5,
            "ai_generated": ai, "created_at": "2026-09-20", "expected_at": "2026-09-26", "days_overdue": days_overdue}


ORDER_ALERTS = {
    "ok": True, "outcome": "ok", "tool": "homs_pharmacy_order_alerts", "arguments": {"alert_type": "all"},
    "duration_ms": 30,
    "result": {"schema_version": "1.0", "ok": True, "tool": "homs_pharmacy_order_alerts", "error": None, "data": {
        "alert_type": "all",
        "counts": {"pending_approval": 16, "ai_suggested_pending": 9, "approved": 20, "ordered": 12, "overdue": 21},
        "pending_approval": [order_row(50, "pending_approval"), order_row(51, "pending_approval", ai=True)],
        "approved": [order_row(16, "approved")],
        "ordered": [order_row(81, "ordered")],
        "overdue": [order_row(16, "approved", days_overdue=25)],
        "source": "student-3-pharmacy-api"}},
}


class OrderAlertTileTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacist', 'staff_id': 3, 'name': 'Demo pharmacist'}

    def render(self, result, alert_type='all'):
        with patch.object(api_client, 'mcp_call', return_value=result) as call:
            html = self.client.post('/mcp/call', data={'tool': 'homs_pharmacy_order_alerts',
                                                       'alert_type': alert_type}).get_data(as_text=True)
        call.assert_called_once_with('homs_pharmacy_order_alerts', {'alert_type': alert_type})
        return html

    def test_order_tiles_and_panels(self):
        html = self.render(ORDER_ALERTS)
        self.assertIn('Purchase-order alerts', html)
        self.assertIn('All open orders', html)
        self.assertEqual(re.findall(r'data-mcp-tile="(\w+)"', html), ['pending', 'ai', 'approved', 'ordered', 'overdue'])
        self.assertIn('<span class="mcp-tile__value">21</span>', html)
        self.assertNotIn('hx-get', html)  # every list comes from the MCP result
        self.assertIn('#50', panel(html, 'pending'))
        ai = panel(html, 'ai')
        self.assertIn('#51', ai)
        self.assertNotIn('#50', ai)
        overdue = panel(html, 'overdue')
        self.assertIn('Days overdue', overdue)
        self.assertIn('<td class="table__num">25</td>', overdue)
        self.assertIn('$250.50', overdue)
        self.assertNotIn('data-mcp-default', html)

    def test_pending_and_overdue_types_open_on_their_list(self):
        for alert_type, title, present, absent in (('pending_approval', 'Pending approval', '#50', 'Days overdue'),
                                                   ('overdue', 'Overdue deliveries', 'Days overdue', '#50')):
            with self.subTest(alert_type=alert_type):
                result = copy.deepcopy(ORDER_ALERTS)
                result['arguments'] = {'alert_type': alert_type}
                data = result['result']['data']
                for name in ('pending_approval', 'approved', 'ordered', 'overdue'):
                    if name != alert_type:
                        data[name] = None
                html = self.render(result, alert_type)
                default = panel(html, 'default')
                self.assertIn(title, default)
                self.assertIn(present, default)
                self.assertNotIn(absent, default)
                self.assertIn('Not included in this request', panel(html, 'approved'))

    def test_dashboard_offers_order_alerts_and_purchase_order_questions(self):
        summary = {"counts": {}, "low_stock": [], "expiring_soon": [], "recent_movements": [], "agent": None}
        with patch.object(api_client, 'dashboard_summary', return_value=summary):
            html = self.client.get('/').get_data(as_text=True)
        for text in ('value="homs_pharmacy_order_alerts"', 'Get purchase-order alerts via MCP', 'Order alerts',
                     'value="overdue"', 'data-rag-example="Who can approve a purchase order?"',
                     'data-rag-example="When is a purchase order overdue?"'):
            self.assertIn(text, html)


class _TableShapes(__import__('html.parser').parser.HTMLParser):
    """Collect, per table, which header and body columns are numeric."""

    def __init__(self):
        super().__init__()
        self.tables, self.row = [], None

    def handle_starttag(self, tag, attrs):
        numeric = 'table__num' in (dict(attrs).get('class') or '')
        if tag == 'table':
            self.tables.append({'head': [], 'rows': []})
        elif tag == 'tr' and self.tables:
            self.row = []
        elif tag == 'th' and self.tables:
            self.tables[-1]['head'].append(numeric)
        elif tag == 'td' and self.row is not None:
            self.row.append(numeric)

    def handle_endtag(self, tag):
        if tag == 'tr' and self.row:
            self.tables[-1]['rows'].append(self.row)
            self.row = None


class NumericColumnAlignmentTests(unittest.TestCase):
    """Numbers are right-aligned, so their headers must be too."""

    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacist', 'staff_id': 3, 'name': 'Demo pharmacist'}

    def assert_aligned(self, html):
        parser = _TableShapes()
        parser.feed(html)
        self.assertTrue(parser.tables)
        for table in parser.tables:
            for row in table['rows']:
                if len(row) == len(table['head']):
                    self.assertEqual(row, table['head'])

    def test_numeric_header_rule_outranks_the_table_default(self):
        # components.css sets ".table thead th { text-align: left }", which beats a bare ".table__num".
        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "main.css").read_text()
        self.assertRegex(css, r"\.table thead th\.table__num\s*\{\s*text-align: right;")

    def test_mcp_result_tables(self):
        for tool, result in (('homs_pharmacy_stock_alerts', STOCK_ALERTS), ('homs_pharmacy_order_alerts', ORDER_ALERTS)):
            with self.subTest(tool=tool), patch.object(api_client, 'mcp_call', return_value=result):
                self.assert_aligned(self.client.post('/mcp/call', data={'tool': tool, 'alert_type': 'all'}).get_data(as_text=True))

    def test_backend_detail_tables(self):
        payload = {"batches": [{"medicine_name": "Ceftriaxone 1g", "batch_number": "PHM-1", "expiry_date": "2026-09-20",
                                "days_until_expiry": -11, "quantity_remaining": 45}], "pagination": {"total_items": 1}}
        with patch.object(api_client, 'list_batches', return_value=payload):
            self.assert_aligned(self.client.get('/mcp/details/expired-batches').get_data(as_text=True))
