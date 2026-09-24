# Issuing and Receiving Stock

## FEFO issuing

Stock is issued First-Expiry, First-Out (FEFO). When a quantity is issued, the
backend sorts the medicine's non-expired batches by expiry date and takes stock
from the batch that expires first, moving to the next batch only when the
earlier one is empty. For example, issuing 12 units when batch A (earliest
expiry) has 8 units and batch B has 20 units takes 8 from A and then 4 from B.
Expired batches are never issued.

An issue request larger than the total available non-expired stock is rejected
and nothing is changed. A successful issue reduces the batch and medicine
quantities and records the per-batch allocations.

## Receiving deliveries

Receiving a delivery requires the medicine, a batch number, an expiry date and
a quantity. It creates the batch or adds to an existing batch, increases the
medicine's stock quantity, and can be linked to an approved or ordered purchase
order, whose received quantity is then updated. Only orders that are approved
or ordered and still have an outstanding quantity can be selected when
receiving.

## Stock movement ledger

Every stock change is recorded as an append-only stock movement with a type,
quantity, the person who performed it and a timestamp. The movement types are:

- `receive` — a delivery was added to stock.
- `issue` — stock was issued to a ward, theatre or emergency department.
- `adjust` — a controlled correction to recorded stock.
- `waste` — stock was written off, for example because a batch expired.

Movements are never edited or deleted, so the ledger is the audit trail for
all inventory changes.
