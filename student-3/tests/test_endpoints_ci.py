"""Post-commit CI/CD endpoint tests for two Student 3 backend/API functions.

These run in ``student-3.yml`` against the real backend container, which is
connected to a freshly seeded database container, so they exercise the full
frontend-independent path: HTTP -> backend business rules -> database service.

1. ``POST /api/stock/issue`` - first-expiry-first-out (FEFO) stock issuing.
2. ``POST /api/purchase-orders/{id}/approve`` - manager approval of an order.

Set ``STUDENT3_BACKEND_URL`` (for example ``http://localhost:5300``) to run
them; without it they are skipped, so the unit-test runs stay self-contained.
"""
import json
import os
import unittest
import urllib.error
import urllib.request

BACKEND_URL = os.environ.get("STUDENT3_BACKEND_URL", "").rstrip("/")
MANAGER = {"X-HOMS-Role": "Pharmacy Manager", "X-HOMS-Name": "CI Pharmacy Manager"}
PHARMACIST = {"X-HOMS-Role": "Pharmacist"}


def call(method, path, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(f"{BACKEND_URL}{path}", data=data, method=method,
                                     headers={**({"Content-Type": "application/json"} if data else {}),
                                              **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def available(medicine_id):
    status, body = call("GET", f"/api/medicines/{medicine_id}")
    assert status == 200, body
    return body["medicine"]["available_quantity"], body["batches"]


@unittest.skipUnless(BACKEND_URL, "set STUDENT3_BACKEND_URL to run the post-commit endpoint tests")
class IssueStockEndpointTests(unittest.TestCase):
    """Endpoint function 1: POST /api/stock/issue."""

    def setUp(self):
        status, body = call("GET", "/api/medicines?page_size=100&status=active")
        self.assertEqual(status, 200, body)
        stocked = [m for m in body["medicines"] if m["available_quantity"] >= 3]
        self.assertTrue(stocked, "the seeded database should have a medicine with stock")
        self.medicine = stocked[0]

    def test_issue_takes_stock_from_the_earliest_expiring_batch(self):
        before, batches = available(self.medicine["medicine_id"])
        usable = sorted((b for b in batches if b["expiry_status"] != "expired" and b["quantity_remaining"] > 0),
                        key=lambda b: b["expiry_date"])
        status, body = call("POST", "/api/stock/issue",
                            {"medicine_id": self.medicine["medicine_id"], "quantity": 2, "reason": "CI ward issue"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["total_issued"], 2)
        self.assertEqual(body["breakdown"][0]["batch_number"], usable[0]["batch_number"])
        self.assertEqual(body["breakdown"][0]["expiry_date"], usable[0]["expiry_date"])
        self.assertEqual(sum(part["quantity_taken"] for part in body["breakdown"]), 2)
        self.assertEqual(available(self.medicine["medicine_id"])[0], before - 2)

    def test_issue_larger_than_stock_is_refused_and_changes_nothing(self):
        before, _ = available(self.medicine["medicine_id"])
        status, body = call("POST", "/api/stock/issue",
                            {"medicine_id": self.medicine["medicine_id"], "quantity": before + 1, "reason": "CI"})
        self.assertEqual(status, 409, body)
        self.assertEqual((body["requested"], body["available"], body["shortfall"]), (before + 1, before, 1))
        self.assertEqual(available(self.medicine["medicine_id"])[0], before)

    def test_invalid_requests_are_rejected(self):
        for payload in ({"medicine_id": self.medicine["medicine_id"], "quantity": 0, "reason": "CI"},
                        {"medicine_id": self.medicine["medicine_id"], "quantity": 1, "reason": " "},
                        {"medicine_id": "abc", "quantity": 1, "reason": "CI"}):
            with self.subTest(payload=payload):
                self.assertEqual(call("POST", "/api/stock/issue", payload)[0], 400)


@unittest.skipUnless(BACKEND_URL, "set STUDENT3_BACKEND_URL to run the post-commit endpoint tests")
class ApprovePurchaseOrderEndpointTests(unittest.TestCase):
    """Endpoint function 2: POST /api/purchase-orders/{id}/approve."""

    def pending_order(self):
        status, body = call("GET", "/api/purchase-orders?status=pending_approval&page_size=100")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["purchase_orders"], "the seeded database should have a pending order")
        return body["purchase_orders"][0]

    def order_status(self, po_id):
        status, body = call("GET", f"/api/purchase-orders/{po_id}")
        self.assertEqual(status, 200, body)
        return body["purchase_order"]["status"]

    def test_only_a_pharmacy_manager_can_approve(self):
        order = self.pending_order()
        for headers in ({}, PHARMACIST):
            with self.subTest(headers=headers):
                status, body = call("POST", f"/api/purchase-orders/{order['po_id']}/approve", {}, headers)
                self.assertEqual(status, 403, body)
        self.assertEqual(self.order_status(order["po_id"]), "pending_approval")

    def test_manager_approves_once_and_a_second_approval_is_refused(self):
        order = self.pending_order()
        status, body = call("POST", f"/api/purchase-orders/{order['po_id']}/approve", {}, MANAGER)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "approved")
        self.assertEqual(self.order_status(order["po_id"]), "approved")
        status, body = call("POST", f"/api/purchase-orders/{order['po_id']}/approve", {}, MANAGER)
        self.assertEqual(status, 409, body)
        self.assertIn("Cannot set approved from approved", body["error"])

    def test_unknown_order_is_not_found(self):
        status, _body = call("POST", "/api/purchase-orders/999999/approve", {}, MANAGER)
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
