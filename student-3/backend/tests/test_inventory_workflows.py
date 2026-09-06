"""Backend inventory workflows with a mocked database-service boundary."""
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from services.ai_client import AIResult
from services import expiry_advisory, reorder_recommendation


def medicine(stock=10):
    return {"medicine_id": 1, "name": "Medicine", "status": "active", "supplier_id": 1,
            "unit_price": 2.0, "stock_quantity": stock, "reorder_level": 10}


class Store:
    def __init__(self, batches=None, orders=None):
        self.medicine = medicine()
        self.batches = batches or []
        self.orders = orders or []
        self.movements = []
        self.calls = []

    def __call__(self, path, method="GET", payload=None):
        self.calls.append((path, method, payload))
        if path == "/medicines/1" and method == "PUT":
            self.medicine.update(payload); return dict(self.medicine)
        if path == "/medicines/1": return dict(self.medicine)
        if path == "/medicines": return [dict(self.medicine)]
        if path == "/suppliers": return [{"supplier_id": 1, "name": "Supplier", "status": "active", "lead_time_days": 2}]
        if path.startswith("/batches?"):
            rows = [dict(row) for row in self.batches if not ("include_empty=false" in path and row["quantity_remaining"] == 0)]
            return sorted(rows, key=lambda row: row["expiry_date"])
        if path == "/batches" and method == "POST":
            row = {**payload, "batch_id": max([b.get("batch_id", 0) for b in self.batches] or [0]) + 1}
            self.batches.append(row); return dict(row)
        if path.startswith("/batches/"):
            batch = next(row for row in self.batches if row["batch_id"] == int(path.rsplit("/", 1)[1]))
            if method == "PUT": batch.update(payload)
            return dict(batch)
        if path == "/stock_movements" and method == "POST":
            row = {**payload, "movement_id": len(self.movements) + 1}; self.movements.append(row); return dict(row)
        if path.startswith("/stock_movements?"): return [row for row in self.movements if row["movement_type"] == "issue"]
        if path == "/purchase_orders": return [dict(row) for row in self.orders]
        if path == "/purchase_orders/1":
            row = self.orders[0]
            if method == "PUT": row.update(payload)
            return dict(row)
        raise AssertionError(f"Unexpected database request: {method} {path}")


class WorkflowTests(unittest.TestCase):
    def call(self, store, path, payload, headers=None):
        with patch.object(app, "database_request", store):
            return app.app.test_client().post(path, json=payload, headers=headers or {})

    def test_fefo_issues_nearest_batch_then_splits_and_updates_stock(self):
        today = date.today()
        store = Store([
            {"batch_id": 2, "medicine_id": 1, "batch_number": "later", "expiry_date": (today + timedelta(days=20)).isoformat(), "quantity_remaining": 7},
            {"batch_id": 1, "medicine_id": 1, "batch_number": "first", "expiry_date": (today + timedelta(days=2)).isoformat(), "quantity_remaining": 3},
        ])
        response = self.call(store, "/api/stock/issue", {"medicine_id": 1, "quantity": 5, "reason": "Ward request"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["breakdown"], [{"batch_number": "first", "expiry_date": (today + timedelta(days=2)).isoformat(), "quantity_taken": 3}, {"batch_number": "later", "expiry_date": (today + timedelta(days=20)).isoformat(), "quantity_taken": 2}])
        self.assertEqual([(row["batch_id"], row["quantity"]) for row in store.movements], [(1, 3), (2, 2)])
        self.assertEqual([row["quantity_remaining"] for row in store.batches], [5, 0])
        self.assertEqual(store.medicine["stock_quantity"], 5)

    def test_issue_rejects_more_than_available_without_writes(self):
        store = Store([{ "batch_id": 1, "medicine_id": 1, "batch_number": "only", "expiry_date": "2030-01-01", "quantity_remaining": 2}])
        response = self.call(store, "/api/stock/issue", {"medicine_id": 1, "quantity": 3, "reason": "Ward request"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(store.movements, [])
        self.assertEqual(store.batches[0]["quantity_remaining"], 2)

    def test_receive_updates_batch_order_received_and_medicine_stock(self):
        store = Store([], [{"po_id": 1, "medicine_id": 1, "supplier_id": 1, "quantity_ordered": 5, "quantity_received": 2, "status": "ordered"}])
        response = self.call(store, "/api/stock/receive", {"medicine_id": 1, "quantity": 3, "batch_number": "new", "expiry_date": (date.today() + timedelta(days=90)).isoformat(), "po_id": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.batches[0]["quantity_remaining"], 3)
        self.assertEqual(store.orders[0]["quantity_received"], 5)
        self.assertEqual(store.orders[0]["status"], "received")
        self.assertEqual(store.medicine["stock_quantity"], 3)

    def test_write_off_records_waste_and_removes_batch_quantity(self):
        store = Store([{ "batch_id": 1, "medicine_id": 1, "batch_number": "expired", "expiry_date": "2020-01-01", "quantity_remaining": 6}])
        response = self.call(store, "/api/batches/1/write-off", {"reason": "Expired"}, {"X-HOMS-Role": app.MANAGER_ROLE})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.movements[0]["movement_type"], "waste")
        self.assertEqual(store.movements[0]["quantity"], 6)
        self.assertEqual(store.batches[0]["quantity_remaining"], 0)


class AdvisoryTests(unittest.TestCase):
    def fetch(self, path):
        today = date.today()
        data = {
            "/medicines": [medicine(stock=1)],
            "/suppliers": [{"supplier_id": 1, "name": "Supplier", "status": "active", "lead_time_days": 2}],
            "/batches?include_expired=false&include_empty=false": [{"batch_id": 1, "medicine_id": 1, "batch_number": "B", "expiry_date": (today + timedelta(days=5)).isoformat(), "quantity_remaining": 1}],
            "/batches?include_expired=true&include_empty=false": [{"batch_id": 1, "medicine_id": 1, "batch_number": "B", "expiry_date": (today + timedelta(days=5)).isoformat(), "quantity_remaining": 10}],
            "/purchase_orders": [],
        }
        if path.startswith("/stock_movements?"):
            return [{"medicine_id": 1, "movement_type": "issue", "quantity": 30}]
        return data[path]

    def test_expiry_ai_success_timeout_and_malformed_response_have_correct_sources(self):
        valid = {"items": [{"recommended_action": "use_first", "priority": "high", "reasoning": "Actual usage is 1.00 units/day, use this batch before expiry."}]}
        with patch.object(expiry_advisory, "run_prompt", return_value=AIResult(True, data=valid)):
            payload, _, _ = expiry_advisory.advisory(self.fetch, 30)
        self.assertEqual(payload["source"], "ai")
        self.assertEqual(payload["items"][0]["quantity_remaining"], 10)
        for result in (AIResult(False, error="Ollama timed out"), AIResult(False, error="invalid JSON")):
            with patch.object(expiry_advisory, "run_prompt", return_value=result):
                payload, _, _ = expiry_advisory.advisory(self.fetch, 30)
            self.assertEqual(payload["source"], "fallback")
            self.assertEqual(payload["items"][0]["quantity_remaining"], 10)

    def test_reorder_ai_success_timeout_malformed_and_open_order_exclusion(self):
        valid = {"items": [{"priority": "high", "reasoning": "Actual usage is 1.00 units/day, cover lead time.", "adjustment_flag": False, "adjustment_reason": None}]}
        with patch.object(reorder_recommendation, "run_prompt", return_value=AIResult(True, data=valid)):
            payload, _, _ = reorder_recommendation.advisory(self.fetch)
        self.assertEqual(payload["source"], "ai")
        self.assertEqual(payload["items"][0]["suggested_quantity"], 9)
        for result in (AIResult(False, error="Ollama timed out"), AIResult(False, error="invalid JSON")):
            with patch.object(reorder_recommendation, "run_prompt", return_value=result):
                payload, _, _ = reorder_recommendation.advisory(self.fetch)
            self.assertEqual(payload["source"], "fallback")
            self.assertEqual(payload["items"][0]["suggested_quantity"], 9)
        def open_order_fetch(path):
            if path == "/purchase_orders":
                return [{"medicine_id": 1, "status": "ordered", "quantity_ordered": 10, "quantity_received": 0}]
            return self.fetch(path)
        self.assertEqual(reorder_recommendation.candidates_for(open_order_fetch), [])


if __name__ == "__main__":
    unittest.main()
