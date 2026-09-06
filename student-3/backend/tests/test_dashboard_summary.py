"""Bulk aggregation tests for the Student 3 inventory dashboard."""
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class DashboardSummaryTests(unittest.TestCase):
    def setUp(self):
        today = date.today()
        self.calls = []
        self.responses = {
            "/medicines": [
                {"medicine_id": 1, "name": "Critical medicine", "status": "active", "stock_quantity": 2, "reorder_level": 10, "supplier_id": 1},
                {"medicine_id": 2, "name": "Adequate medicine", "status": "active", "stock_quantity": 20, "reorder_level": 5, "supplier_id": 2},
                {"medicine_id": 3, "name": "Inactive medicine", "status": "discontinued", "stock_quantity": 0, "reorder_level": 10, "supplier_id": 1},
            ],
            "/suppliers": [{"supplier_id": 1, "name": "Alpha Supply"}, {"supplier_id": 2, "name": "Beta Supply"}],
            "/batches?include_expired=true&include_empty=false": [
                {"batch_id": 1, "medicine_id": 1, "batch_number": "B-7", "expiry_date": (today + timedelta(days=7)).isoformat(), "quantity_remaining": 4},
                {"batch_id": 2, "medicine_id": 2, "batch_number": "B-20", "expiry_date": (today + timedelta(days=20)).isoformat(), "quantity_remaining": 6},
                {"batch_id": 3, "medicine_id": 1, "batch_number": "B-old", "expiry_date": (today - timedelta(days=1)).isoformat(), "quantity_remaining": 3},
            ],
            "/purchase_orders": [{"status": "pending_approval"}, {"status": "approved"}],
            "/stock_movements": [
                {"medicine_id": 2, "movement_type": "receive", "quantity": 5, "performed_by": "Kim", "created_at": "2026-01-01T08:00:00"},
                {"medicine_id": 1, "movement_type": "issue", "quantity": 2, "performed_by": "Sam", "created_at": "2026-01-02T08:00:00"},
            ],
        }

    def database_request(self, path, method="GET", payload=None):
        self.assertEqual((method, payload), ("GET", None))
        self.calls.append(path)
        return self.responses[path]

    def test_summary_uses_five_bulk_reads_and_returns_dashboard_shapes(self):
        agent_status = {"enabled": True, "interval_seconds": 300}
        with patch.object(app, "database_request", self.database_request), patch.object(app.agent, "status", return_value=agent_status):
            response = app.app.test_client().get("/api/dashboard/summary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, ["/medicines", "/suppliers", "/batches?include_expired=true&include_empty=false", "/purchase_orders", "/stock_movements"])
        payload = response.get_json()
        self.assertEqual(payload["counts"], {"active_medicines": 2, "low_stock": 1,
                                               "expiring_within_30_days": 2, "expiring_within_7_days": 1,
                                               "expired_batches": 1, "pending_approvals": 1})
        self.assertEqual(payload["low_stock"], [{"name": "Critical medicine", "stock_quantity": 2,
                                                   "reorder_level": 10, "supplier_name": "Alpha Supply"}])
        self.assertEqual([row["batch_number"] for row in payload["expiring_soon"]], ["B-7", "B-20"])
        self.assertEqual(payload["recent_movements"][0]["medicine_name"], "Critical medicine")
        self.assertEqual(payload["recent_movements"][0]["movement_type"], "issue")
        self.assertEqual(payload["agent"], agent_status)

    def test_database_error_is_returned_without_partial_summary(self):
        error = app.DatabaseServiceError(503, "Database service unavailable")
        with patch.object(app, "database_request", side_effect=error):
            response = app.app.test_client().get("/api/dashboard/summary")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"error": "Database service unavailable"})


if __name__ == "__main__":
    unittest.main()
