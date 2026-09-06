"""Dashboard rendering tests using only the frontend-to-backend client boundary."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import api_client


def summary():
    return {
        "counts": {"low_stock": 2, "expiring_within_7_days": 1, "expired_batches": 3, "pending_approvals": 4},
        "low_stock": [{"name": "Amoxicillin", "stock_quantity": 2, "reorder_level": 10, "supplier_name": "Alpha"}],
        "expiring_soon": [{"medicine_name": "Amoxicillin", "batch_number": "LOT-7", "expiry_date": "2026-09-13", "quantity_remaining": 4, "days_until_expiry": 7}],
        "recent_movements": [{"medicine_name": "Amoxicillin", "movement_type": "issue", "quantity": 2, "performed_by": "Sam", "created_at": "2026-09-06T08:00:00"}],
        "agent": {"enabled": True, "interval_seconds": 60, "max_proposals": 3, "budget_cap": "500.00", "last_cycle_time": None, "running": False, "proposals_created": 1, "observe_rejected": 2, "last_error": None},
    }


class DashboardTests(unittest.TestCase):
    def client_for(self, role):
        client = app.app.test_client()
        with client.session_transaction() as session:
            session["demo_identity"] = {"role": role, "staff_id": 1, "name": "Demo user"}
        return client

    def test_manager_dashboard_has_live_inventory_data_and_correct_filters(self):
        with patch.object(api_client, "dashboard_summary", return_value=summary()) as request_summary:
            response = self.client_for(app.ROLE_MANAGER).get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        request_summary.assert_called_once_with()
        for value in ("Pharmacy Inventory Dashboard", "Current inventory", "Amoxicillin", "LOT-7", "Sam", "Every 60 seconds"):
            self.assertIn(value, html)
        for target in ("/medicines?stock_status=low", "/batches?expiry_status=expiring_7",
                       "/batches?expiry_status=expired", "/purchase-orders?status=pending_approval"):
            self.assertIn(target, html)
        self.assertNotIn("Patient", html)
        self.assertNotIn("Approve</button>", html)

    def test_pharmacist_cannot_see_pending_approval_tile(self):
        with patch.object(api_client, "dashboard_summary", return_value=summary()):
            html = self.client_for(app.ROLE_PHARMACIST).get("/").get_data(as_text=True)
        self.assertNotIn("Pending Approvals", html)
        self.assertNotIn("/purchase-orders?status=pending_approval", html)

    def test_backend_failure_shows_dashboard_error_without_mock_data(self):
        with patch.object(api_client, "dashboard_summary", side_effect=api_client.BackendError("Backend unavailable")):
            html = self.client_for(app.ROLE_MANAGER).get("/").get_data(as_text=True)
        self.assertIn("Backend unavailable", html)
        self.assertIn("No active medicines are at or below", html)
        self.assertNotIn("John Smith", html)


if __name__ == "__main__":
    unittest.main()
