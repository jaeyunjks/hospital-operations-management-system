# Pharmacy Inventory and Low Stock

## Medicine records

Each medicine record in the pharmacy inventory holds a name, category, unit,
unit price, stock quantity, reorder level, optional storage instructions and a
linked supplier. A medicine is either `active` or `discontinued`. Discontinued
medicines are hidden from normal lists and can be reactivated by a Pharmacy
Manager. Medicine names must be unique, ignoring letter case.

## Low stock rule

An active medicine is low stock when its stock quantity is at or below its
reorder level. For example, a hypothetical medicine with 20 units in stock and
a reorder level of 200 would be low stock. The dashboard counts all low-stock
medicines and lists the ten with the largest shortfall first (reorder level
minus stock quantity). Low stock does not create an order by itself; it makes
the medicine a candidate for a reorder suggestion.

## Current stock figures are not in this knowledge base

These documents describe pharmacy rules and workflows only. They do not record
how many units of any medicine are in stock, which batches currently exist, or
how many orders are pending, because those figures change constantly. Current
figures come from the live pharmacy data: the dashboard, the Medicines and
Batches pages, and the "Get stock alerts via MCP" tool.

## Roles and permissions

The pharmacy feature has two demonstration roles:

- **Pharmacy Manager** — can create, edit, discontinue and reactivate medicines
  and suppliers, write off batches, create and edit purchase orders, and
  approve, reject, mark ordered or cancel purchase orders.
- **Pharmacist** — can view inventory, issue stock, receive deliveries and
  request read-only AI advice, but cannot approve orders or write off stock.

The role is selected on a demonstration entry page. It is not a real login or
shared authentication system.

## Dashboard

The pharmacy dashboard shows counts of active medicines, low-stock medicines,
batches expiring within 30 days and within 7 days, expired batches, and
purchase orders pending approval (the pending count is shown to managers). It
also lists the nearest expiring batches and the ten most recent stock
movements.
