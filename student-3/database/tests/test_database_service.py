"""Temporary-file tests for Student 3's SQLite pharmacy service."""
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app as database_app
import db
import init_db


class TemporaryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "pharmacy-test.db"
        with db.get_connection(self.path) as connection:
            db.apply_schema(connection)
        self.path_patch = patch.object(database_app.db, "get_database_path", return_value=self.path)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.tempdir.cleanup)
        self.client = database_app.app.test_client()

    def seed_parent_rows(self):
        with db.get_connection(self.path) as connection:
            supplier_id = connection.execute("INSERT INTO suppliers (name, lead_time_days) VALUES ('Supplier', 2)").lastrowid
            medicine_id = connection.execute("INSERT INTO medicines (name, category, unit, unit_price, reorder_level, supplier_id) VALUES ('Medicine', 'Test', 'unit', 1.5, 4, ?)", (supplier_id,)).lastrowid
        return supplier_id, medicine_id

    def test_schema_and_seed_data_meet_minimum_specification(self):
        init_db.initialise(str(self.path))
        with db.get_connection(self.path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(set(init_db.EXPECTED_TABLES) <= tables)
            for table in init_db.EXPECTED_TABLES:
                self.assertGreaterEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 10, table)
        init_db.check(str(self.path))

    def test_schema_crud_for_staff_supplier_medicine_batch_and_purchase_order(self):
        supplier_id, medicine_id = self.seed_parent_rows()
        with db.get_connection(self.path) as connection:
            staff_id = connection.execute("INSERT INTO staff (name, role, notes) VALUES ('Taylor', 'staff', 'night')").lastrowid
            self.assertEqual(connection.execute("SELECT notes FROM staff WHERE staff_id=?", (staff_id,)).fetchone()[0], "night")
            connection.execute("UPDATE staff SET notes='day' WHERE staff_id=?", (staff_id,))
            self.assertEqual(connection.execute("SELECT notes FROM staff WHERE staff_id=?", (staff_id,)).fetchone()[0], "day")

            connection.execute("UPDATE suppliers SET name='Updated supplier' WHERE supplier_id=?", (supplier_id,))
            self.assertEqual(connection.execute("SELECT name FROM suppliers WHERE supplier_id=?", (supplier_id,)).fetchone()[0], "Updated supplier")
            connection.execute("UPDATE medicines SET stock_quantity=9 WHERE medicine_id=?", (medicine_id,))
            self.assertEqual(connection.execute("SELECT stock_quantity FROM medicines WHERE medicine_id=?", (medicine_id,)).fetchone()[0], 9)

            batch_id = connection.execute("INSERT INTO batches (medicine_id, batch_number, expiry_date, quantity_received, quantity_remaining, received_at) VALUES (?, 'B-1', '2030-01-01', 8, 8, '2026-01-01')", (medicine_id,)).lastrowid
            connection.execute("UPDATE batches SET quantity_remaining=6 WHERE batch_id=?", (batch_id,))
            self.assertEqual(connection.execute("SELECT quantity_remaining FROM batches WHERE batch_id=?", (batch_id,)).fetchone()[0], 6)

            po_id = connection.execute("INSERT INTO purchase_orders (medicine_id, supplier_id, quantity_ordered, unit_price, status, created_at) VALUES (?, ?, 5, 1.5, 'draft', '2026-01-01')", (medicine_id, supplier_id)).lastrowid
            connection.execute("UPDATE purchase_orders SET status='pending_approval' WHERE po_id=?", (po_id,))
            self.assertEqual(connection.execute("SELECT status FROM purchase_orders WHERE po_id=?", (po_id,)).fetchone()[0], "pending_approval")

            connection.execute("DELETE FROM purchase_orders WHERE po_id=?", (po_id,))
            connection.execute("DELETE FROM batches WHERE batch_id=?", (batch_id,))
            connection.execute("DELETE FROM medicines WHERE medicine_id=?", (medicine_id,))
            connection.execute("DELETE FROM suppliers WHERE supplier_id=?", (supplier_id,))
            connection.execute("DELETE FROM staff WHERE staff_id=?", (staff_id,))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM staff WHERE staff_id=?", (staff_id,)).fetchone()[0], 0)

    def test_stock_movement_create_and_read_and_foreign_keys_are_enforced(self):
        supplier_id, medicine_id = self.seed_parent_rows()
        with db.get_connection(self.path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO batches (medicine_id, batch_number, expiry_date, quantity_received, quantity_remaining, received_at) VALUES (99999, 'bad', '2030-01-01', 1, 1, '2026-01-01')")
            movement_id = connection.execute("INSERT INTO stock_movements (medicine_id, movement_type, quantity, created_at) VALUES (?, 'receive', 3, '2026-01-01')", (medicine_id,)).lastrowid
            self.assertEqual(connection.execute("SELECT quantity FROM stock_movements WHERE movement_id=?", (movement_id,)).fetchone()[0], 3)

    def test_http_crud_validation_and_soft_delete(self):
        supplier = self.client.post("/suppliers", json={"name": "HTTP supplier", "lead_time_days": 2})
        self.assertEqual(supplier.status_code, 201)
        supplier_id = supplier.get_json()["supplier_id"]
        self.assertEqual(self.client.put(f"/suppliers/{supplier_id}", json={"lead_time_days": 3}).get_json()["lead_time_days"], 3)
        self.assertEqual(self.client.get(f"/suppliers/{supplier_id}").status_code, 200)
        self.assertEqual(self.client.post("/suppliers", json={"name": "bad", "lead_time_days": -1}).status_code, 400)
        self.assertEqual(self.client.get("/suppliers/999999").status_code, 404)

        medicine = self.client.post("/medicines", json={"name": "HTTP medicine", "category": "Test", "unit": "tablet", "unit_price": 1, "stock_quantity": 5, "reorder_level": 3, "supplier_id": supplier_id})
        self.assertEqual(medicine.status_code, 201)
        medicine_id = medicine.get_json()["medicine_id"]
        self.assertEqual(self.client.put(f"/medicines/{medicine_id}", json={"stock_quantity": 4}).status_code, 200)
        self.assertEqual(self.client.post("/medicines", json={"name": "missing fields"}).status_code, 400)
        self.assertEqual(self.client.get("/medicines/999999").status_code, 404)

        batch_payload = {"medicine_id": medicine_id, "batch_number": "HTTP-B", "expiry_date": (date.today() + timedelta(days=10)).isoformat(), "quantity_received": 5, "quantity_remaining": 5, "received_at": "2026-01-01"}
        batch = self.client.post("/batches", json=batch_payload)
        self.assertEqual(batch.status_code, 201)
        batch_id = batch.get_json()["batch_id"]
        self.assertEqual(self.client.put(f"/batches/{batch_id}", json={"quantity_remaining": 2}).get_json()["quantity_remaining"], 2)
        self.assertEqual(self.client.post("/batches", json={**batch_payload, "medicine_id": 999999, "batch_number": "bad"}).status_code, 404)
        self.assertEqual(self.client.get("/batches/999999").status_code, 404)

        po_payload = {"medicine_id": medicine_id, "supplier_id": supplier_id, "quantity_ordered": 4, "unit_price": 1, "created_at": "2026-01-01"}
        purchase_order = self.client.post("/purchase_orders", json=po_payload)
        self.assertEqual(purchase_order.status_code, 201)
        po_id = purchase_order.get_json()["po_id"]
        self.assertEqual(self.client.put(f"/purchase_orders/{po_id}", json={"status": "approved"}).get_json()["status"], "approved")
        self.assertEqual(self.client.post("/purchase_orders", json={"medicine_id": medicine_id}).status_code, 400)
        self.assertEqual(self.client.get("/purchase_orders/999999").status_code, 404)

        movement = self.client.post("/stock_movements", json={"medicine_id": medicine_id, "batch_id": batch_id, "movement_type": "receive", "quantity": 2, "created_at": "2026-01-01"})
        self.assertEqual(movement.status_code, 201)
        self.assertEqual(self.client.post("/stock_movements", json={"medicine_id": medicine_id, "movement_type": "unknown", "quantity": 1, "created_at": "2026-01-01"}).status_code, 400)
        self.assertEqual(self.client.put(f"/stock_movements/{movement.get_json()['movement_id']}", json={}).status_code, 405)
        self.assertEqual(self.client.delete(f"/stock_movements/{movement.get_json()['movement_id']}").status_code, 405)

        deleted = self.client.delete(f"/medicines/{medicine_id}")
        self.assertEqual(deleted.get_json(), {"medicine_id": medicine_id, "status": "discontinued"})
        remaining = self.client.get(f"/medicines/{medicine_id}").get_json()
        self.assertEqual(remaining["status"], "discontinued")
        self.assertNotEqual(self.client.delete("/medicines/999999").status_code, 200)


if __name__ == "__main__":
    unittest.main()
