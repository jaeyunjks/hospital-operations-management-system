"""Release 1 context added to a reorder review: live MCP order alerts and RAG policy.

Before a reorder decision is sent to the Multi-Agent Server, the backend asks the
shared MCP server for the pharmacy's purchase-order alerts and the shared RAG
server for the reorder and approval rules. Both are optional: when MCP or RAG is
disabled or unavailable the review still runs and the evidence says what is missing.
"""
from __future__ import annotations

from typing import Any

from services import mcp_client, rag_client

ORDER_ALERTS_TOOL = "homs_pharmacy_order_alerts"
ORDER_LISTS = ("pending_approval", "approved", "ordered", "overdue")
# Asked separately: one cited answer per rule is more reliable than one combined question.
POLICY_QUESTIONS = ("How is the suggested reorder quantity calculated?",
                    "Who must approve a purchase order?")


def mcp_order_context(medicine_name: str) -> dict[str, Any] | None:
    """Pharmacy-wide order alerts and any listed orders for this medicine, via MCP."""
    result = mcp_client.call_tool(ORDER_ALERTS_TOOL, {"alert_type": "all"})
    envelope = result.result if isinstance(result.result, dict) else {}
    data = envelope.get("data") if envelope.get("ok") else None
    if not result.ok or not isinstance(data, dict) or not isinstance(data.get("counts"), dict):
        return None
    orders: dict[Any, dict[str, Any]] = {}
    for name in ORDER_LISTS:
        for order in data.get(name) or []:
            if isinstance(order, dict) and order.get("medicine_name") == medicine_name:
                orders[order.get("po_id")] = {
                    "po_id": order.get("po_id"), "status": order.get("status"),
                    "outstanding": order.get("outstanding"), "expected_at": order.get("expected_at"),
                    "days_overdue": order.get("days_overdue") or 0,
                }
    return {
        "source": f"MCP tool {ORDER_ALERTS_TOOL}",
        "pharmacy_overdue_deliveries": data["counts"].get("overdue"),
        "pharmacy_pending_approval": data["counts"].get("pending_approval"),
        "listed_orders_for_this_medicine": sorted(orders.values(), key=lambda order: order["po_id"] or 0),
        "note": "MCP lists up to 10 orders per category; counts cover all orders.",
    }


def rag_policy_context() -> dict[str, Any] | None:
    """The pharmacy's reorder and approval rules, answered from the knowledge base via RAG."""
    answers = []
    for question in POLICY_QUESTIONS:
        result = rag_client.ask(question)
        if result.ok and result.outcome == "answered" and result.answer:
            answers.append({
                "question": question,
                "answer": result.answer,
                "confidence": result.confidence,
                "citations": [f"{item['title']} > {item['section']}" for item in result.citations],
            })
    return {"source": "RAG knowledge base (student-3)", "answers": answers} if answers else None
