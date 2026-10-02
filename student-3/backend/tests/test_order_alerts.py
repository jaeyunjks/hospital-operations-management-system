"""Read-only purchase-order alerts used by the MCP order-alerts tool."""
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


def po(po_id, status, *, days_from_today, ai=0, created="2026-09-01T00:00:00"):
    return {"po_id": po_id, "medicine_id": 1, "supplier_id": 1, "status": status, "quantity_ordered": 10,
            "quantity_received": 0, "unit_price": 2.5, "ai_generated": ai, "created_at": created,
            "created_by": "Olivia Martin", "approved_by": None, "ai_reasoning": "internal", "decision_reason": None,
            "expected_at": (date.today() + timedelta(days=days_from_today)).isoformat()}


class OrderAlertTests(unittest.TestCase):
    def setUp(self):
        self.orders = [
            po(1, "pending_approval", days_from_today=-3, ai=1, created="2026-09-02T00:00:00"),
            po(2, "pending_approval", days_from_today=5, created="2026-09-01T00:00:00"),
            po(3, "approved", days_from_today=-4),
            po(4, "ordered", days_from_today=-1),
            po(5, "ordered", days_from_today=6),
            po(6, "draft", days_from_today=-9),
            po(7, "received", days_from_today=-9),
        ]
        responses = {"/medicines": [{"medicine_id": 1, "name": "Paracetamol 500mg", "unit": "tablet"}],
                     "/suppliers": [{"supplier_id": 1, "name": "MedSupply Australia", "lead_time_days": 5}],
                     "/purchase_orders": self.orders}
        patcher = patch.object(app, "database_request", lambda path, *a, **k: responses[path])
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self):
        response = app.app.test_client().get("/api/purchase-orders/alerts")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_counts_cover_open_orders_and_late_deliveries_only(self):
        body = self.get()
        self.assertEqual(body["counts"], {"pending_approval": 2, "ai_suggested_pending": 1, "approved": 1,
                                          "ordered": 2, "overdue": 2})

    def test_lists_are_ordered_and_allowlisted(self):
        body = self.get()
        self.assertEqual([o["po_id"] for o in body["pending_approval"]], [2, 1])  # oldest first
        self.assertEqual([o["po_id"] for o in body["overdue"]], [3, 4])  # most overdue first
        self.assertEqual(body["overdue"][0]["days_overdue"], 4)
        self.assertEqual(body["pending_approval"][1]["ai_generated"], True)
        for order in body["pending_approval"] + body["overdue"]:
            self.assertEqual(set(order), {"po_id", "medicine_name", "supplier_name", "status", "quantity_ordered",
                                          "outstanding", "total_value", "ai_generated", "created_at",
                                          "expected_at", "days_overdue"})
        self.assertEqual(body["pending_approval"][0]["created_at"], "2026-09-01")

    def test_pending_orders_past_their_date_are_not_late_deliveries(self):
        body = self.get()
        self.assertNotIn(1, [o["po_id"] for o in body["overdue"]])

    def test_lists_are_capped_at_ten(self):
        self.orders[:] = [po(n, "approved", days_from_today=-n) for n in range(1, 15)]
        body = self.get()
        self.assertEqual((body["counts"]["approved"], len(body["approved"])), (14, 10))
        self.assertEqual((body["counts"]["overdue"], len(body["overdue"])), (14, 10))

    def test_database_failure_is_reported(self):
        def fail(path, *a, **k):
            raise app.DatabaseServiceError(503, "Database service unavailable")
        with patch.object(app, "database_request", fail):
            response = app.app.test_client().get("/api/purchase-orders/alerts")
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
