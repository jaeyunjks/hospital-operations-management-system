"""Frontend route smoke tests with every backend API call mocked."""
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import api_client


MEDICINE = {"medicine_id": 1, "name": "Medicine", "category": "Test", "unit": "tablet", "unit_price": 1.0,
            "stock_quantity": 5, "available_quantity": 5, "reorder_level": 10, "supplier_id": 1,
            "supplier_name": "Supplier", "status": "active", "batch_count": 1, "earliest_expiry": "2030-01-01"}
SUPPLIER = {"supplier_id": 1, "name": "Supplier", "contact_email": "s@example.com", "phone": "1",
            "lead_time_days": 2, "status": "active", "medicines_supplied": 1, "open_orders": 0, "total_ordered_value": 0}
BATCH = {"batch_id": 1, "medicine_id": 1, "medicine_name": "Medicine", "batch_number": "B1", "expiry_date": "2030-01-01",
         "quantity_received": 5, "quantity_remaining": 5, "days_until_expiry": 365, "estimated_value": 5, "expiry_status": "valid"}
MOVEMENT = {"movement_id": 1, "medicine_id": 1, "medicine_name": "Medicine", "movement_type": "receive", "quantity": 5,
            "batch_number": "B1", "reason": "Delivery", "performed_by": "Manager", "created_at": "2026-01-01T00:00:00"}
ORDER = {"po_id": 1, "medicine_id": 1, "medicine_name": "Medicine", "medicine_unit": "tablet", "supplier_id": 1,
         "supplier_name": "Supplier", "quantity_ordered": 5, "quantity_received": 0, "unit_price": 1, "total_value": 5,
         "expected_at": "2030-01-01", "status": "pending_approval", "ai_generated": 1, "ai_reasoning": "Actual usage is 1.00 units/day.", "is_overdue": False, "supplier_lead_time_days": 2}
PAGINATION = {"page": 1, "page_size": 10, "total_items": 1, "total_pages": 1, "has_previous": False, "has_next": False}
AGENT = {"enabled": True, "interval_seconds": 60, "max_proposals": 3, "budget_cap": "500", "last_cycle_time": None, "running": False, "proposals_created": 0, "observe_rejected": 0, "last_error": None}


class FrontendRouteSmokeTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["demo_identity"] = {"staff_id": 1, "name": "Manager", "role": app.ROLE_MANAGER}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        patches = {
            "list_staff": lambda: [{"staff_id": 1, "name": "Manager", "role": "manager"}],
            "get_staff": lambda _id: {"staff_id": 1, "name": "Manager", "role": "manager"},
            "list_medicines": lambda **_kwargs: {"medicines": [MEDICINE], "categories": ["Test"], "pagination": PAGINATION},
            "get_medicine": lambda _id: {"medicine": MEDICINE, "batches": [BATCH], "stock_movements": [MOVEMENT], "supplier": SUPPLIER, "purchase_orders": [ORDER]},
            "list_suppliers": lambda _status="active", _search="", **kwargs: {"suppliers": [SUPPLIER], "pagination": PAGINATION} if kwargs else [SUPPLIER],
            "get_supplier": lambda _id: {"supplier": SUPPLIER, "medicines": [MEDICINE], "purchase_orders": [ORDER]},
            "list_batches": lambda **_kwargs: {"batches": [BATCH], "summary": {}, "pagination": PAGINATION},
            "list_stock_movements": lambda **_kwargs: {"movements": [MOVEMENT], "summary": {}, "pagination": PAGINATION},
            "list_purchase_orders": lambda **_kwargs: {"purchase_orders": [ORDER], "summary": {"pending_approval": {"count": 1, "total_value": 5}}, "pagination": PAGINATION},
            "get_purchase_order": lambda _id: ORDER,
            "agent_status": lambda: AGENT,
            "dashboard_summary": lambda: {"counts": {"low_stock": 1, "expiring_within_7_days": 0, "expired_batches": 0, "pending_approvals": 1}, "low_stock": [MEDICINE], "expiring_soon": [], "recent_movements": [MOVEMENT], "agent": AGENT},
        }
        for name, value in patches.items():
            self.stack.enter_context(patch.object(api_client, name, value))

    def test_every_rendering_get_route_returns_200_without_live_backend(self):
        routes = ["/", "/demo", "/medicines", "/medicines/table", "/medicines/1/detail", "/medicines/form", "/medicines/export",
                  "/batches", "/batches/table", "/batches/export", "/movements", "/movements/table", "/movements/summary", "/movements/export",
                  "/suppliers", "/suppliers/table", "/suppliers/1/detail", "/suppliers/form", "/suppliers/export",
                  "/purchase-orders", "/purchase-orders/table", "/purchase-orders/agent", "/purchase-orders/1/detail", "/purchase-orders/export", "/health"]
        for route in routes:
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_non_manager_has_no_mutating_controls(self):
        with self.client.session_transaction() as session:
            session["demo_identity"]["role"] = app.ROLE_PHARMACIST
            session.modified = True
        for route in ("/medicines", "/batches", "/suppliers", "/purchase-orders"):
            html = self.client.get(route).get_data(as_text=True)
            self.assertNotIn("Add medicine", html)
            self.assertNotIn("Approve</button>", html)
        self.assertEqual(self.client.get("/medicines/form").status_code, 403)
        self.assertEqual(self.client.get("/suppliers/form").status_code, 403)

    def test_timeout_and_connection_errors_render_error_not_500(self):
        for endpoint, dependency in (("/", "dashboard_summary"), ("/medicines", "list_medicines"),
                                     ("/batches", "list_batches"), ("/movements", "list_stock_movements"),
                                     ("/suppliers", "list_suppliers"), ("/purchase-orders", "list_purchase_orders")):
            with self.subTest(endpoint=endpoint), patch.object(api_client, dependency, side_effect=api_client.BackendError("connection timed out", 504)):
                response = self.client.get(endpoint)
                self.assertNotEqual(response.status_code, 500)
                self.assertIn("connection timed out", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
