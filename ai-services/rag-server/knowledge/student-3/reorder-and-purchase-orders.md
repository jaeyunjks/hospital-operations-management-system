# Reordering and Purchase Orders

## When a medicine is reordered

A medicine becomes a reorder candidate when its available stock is at or below
its reorder level and its supplier is active. Medicines that already have an
open purchase order (pending approval, approved or ordered) are not suggested
again, which prevents duplicate orders.

## Suggested reorder quantity

The backend, not the AI model, calculates the suggested quantity:

1. The daily usage rate is the stock issued in the last 30 days divided by 30.
2. Stock expiring within the supplier's lead time is treated as unusable.
3. The coverage target is the larger of the reorder level and the daily usage
   rate multiplied by (lead time + 7 days).
4. The suggested quantity is the coverage target minus usable stock minus any
   quantity already on open orders.

A medicine is only suggested when this quantity is greater than zero.

## Purchase order lifecycle

A purchase order moves through these statuses:

| Status | Meaning |
|---|---|
| `draft` | Created but not yet submitted. |
| `pending_approval` | Waiting for a Pharmacy Manager decision. |
| `approved` | Approved by a manager; not yet sent to the supplier. |
| `ordered` | Sent to the supplier; stock can be received against it. |
| `received` | The ordered quantity has been received. |
| `rejected` | Declined by a manager. |
| `cancelled` | Withdrawn before completion. |

Allowed manager transitions: only `pending_approval` orders can be approved or
rejected, only `approved` orders can be marked ordered, and `draft`,
`pending_approval`, `approved` or `ordered` orders can be cancelled. Rejecting
or cancelling requires a decision reason. Only draft or pending-approval orders
can be edited.

An order is overdue when its expected delivery date has passed and it is not
yet received, rejected or cancelled. The expected date is based on the
supplier's lead time.

## Human approval

Every purchase order, including AI-suggested and agent-proposed orders, needs a
Pharmacy Manager's approval before it can be ordered. No AI component approves,
places or receives an order.
