"""Readable money and status text on the purchase-order and supplier screens."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import api_client


class DisplayFilterTests(unittest.TestCase):
    def test_money_uses_two_decimals_and_keeps_sub_cent_unit_prices(self):
        for value, shown in ((76.0, "$76.00"), (64531.509999999995, "$64531.51"), ("500.00", "$500.00"),
                             (0, "$0.00"), (0.006, "$0.006"), (None, "—"), ("n/a", "—")):
            with self.subTest(value=value):
                self.assertEqual(app.money(value), shown)

    def test_status_label_is_readable(self):
        for value, shown in (("pending_approval", "Pending approval"), ("approved", "Approved"), (None, "")):
            with self.subTest(value=value):
                self.assertEqual(app.status_label(value), shown)

    def test_purchase_order_table_shows_readable_status_and_money(self):
        client = app.app.test_client()
        with client.session_transaction() as session:
            session['demo_identity'] = {'role': 'Pharmacist', 'staff_id': 3, 'name': 'Demo pharmacist'}
        order = {"po_id": 82, "ai_generated": 1, "medicine_name": "Azithromycin 250mg", "supplier_name": "WestCare Medical",
                 "quantity_ordered": 40, "quantity_received": 0, "unit_price": 6.71, "total_value": 268.4,
                 "is_overdue": False, "expected_at": "2026-10-13", "status": "pending_approval"}
        payload = {"purchase_orders": [order],
                   "pagination": {"page": 1, "page_size": 25, "total_items": 1, "total_pages": 1}}
        with patch.object(api_client, 'list_purchase_orders', return_value=payload):
            html = client.get('/purchase-orders/table').get_data(as_text=True)
        for text in ('<td>$6.71</td><td>$268.40</td>', '<span class="badge badge-neutral">Pending approval</span>'):
            self.assertIn(text, html)
        self.assertNotIn('>pending_approval<', html)


if __name__ == "__main__":
    unittest.main()
