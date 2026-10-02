# Pharmacy & Medication Inventory Overview

## What the pharmacy feature does

The pharmacy feature (Student 3) manages the hospital's medication inventory.
Pharmacy staff use it to keep medicine and supplier records, track every batch
and its expiry date, issue stock to wards, theatres and the emergency
department, receive deliveries, write off expired or damaged stock, and order
new stock through a purchase-order workflow that a Pharmacy Manager approves.

## Main pages

- **Dashboard** — shared MCP and RAG tools, then low-stock, expiring and expired
  batch counts, pending approvals, the scheduled agent's status and recent
  stock movements.
- **Medicines** — medicine records with stock levels, reorder levels and
  suppliers; managers add, edit, discontinue and reactivate medicines.
- **Batches & Expiry** — every batch with its expiry status; managers write off
  batches and anyone can request the AI expiry advisory.
- **Stock Movements** — the append-only ledger of receipts, issues, adjustments
  and waste.
- **Purchase Orders** — draft, approve, reject, order, receive and cancel orders,
  including AI-suggested and agent-proposed drafts.
- **Suppliers** — supplier records with lead times; managers maintain them.

## How AI is used

AI only advises; it never changes inventory by itself. The expiry advisory and
reorder suggestions add judgement to backend-calculated figures, the scheduled
agent drafts orders that still need a manager's approval, the MCP tools read
current stock alerts, and the pharmacy assistant answers questions from these
documents with citations.
