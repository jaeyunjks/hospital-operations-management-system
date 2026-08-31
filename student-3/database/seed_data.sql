PRAGMA foreign_keys = ON;

-- SUPPLIERS

INSERT INTO suppliers
    (supplier_id, name, contact_email, phone, lead_time_days, status)
VALUES
    (1, 'MedSupply Australia', 'orders@medsupply.example.com', '02-9000-1001', 5, 'active'),
    (2, 'HealthCore Pharmaceuticals', 'sales@healthcore.example.com', '03-9000-1002', 8, 'active'),
    (3, 'Pacific Medical Distributors', 'orders@pacificmedical.example.com', '07-9000-1003', 12, 'active'),
    (4, 'Southern Clinical Supplies', 'contact@southernclinical.example.com', '08-9000-1004', 6, 'active');

-- MEDICINES
-- stock_quantity represents current stock across non-expired
-- batches and must be updated whenever stock is received,
-- issued, or written off.

INSERT INTO medicines
    (medicine_id, name, category, unit, unit_price, stock_quantity,
     reorder_level, storage_instructions, supplier_id, status)
VALUES
    (1, 'Paracetamol 500mg', 'Analgesic', 'tablet', 0.12, 180, 200,
     'Store below 25°C in a dry place', 1, 'active'),

    (2, 'Ibuprofen 400mg', 'Analgesic', 'tablet', 0.18, 95, 120,
     'Store below 25°C in a dry place', 1, 'active'),

    (3, 'Amoxicillin 500mg', 'Antibiotic', 'capsule', 0.42, 420, 150,
     'Store below 25°C and protect from moisture', 2, 'active'),

    (4, 'Ceftriaxone 1g', 'Antibiotic', 'vial', 4.80, 42, 50,
     'Store below 25°C', 2, 'active'),

    (5, 'Propofol 10mg/mL', 'Anaesthetic', 'vial', 8.50, 28, 35,
     'Store at 2–8°C. Protect from light.', 3, 'active'),

    (6, 'Lidocaine 2%', 'Anaesthetic', 'vial', 2.75, 60, 40,
     'Store below 25°C', 3, 'active'),

    (7, 'Ondansetron 4mg', 'Antiemetic', 'tablet', 0.95, 75, 100,
     'Store below 25°C', 2, 'active'),

    (8, 'Metoclopramide 10mg', 'Antiemetic', 'ampoule', 1.15, 96, 60,
     'Store below 25°C', 4, 'active'),

    (9, 'Sodium Chloride 0.9%', 'IV Fluid', 'ml', 0.006, 8500, 5000,
     'Store below 25°C', 4, 'active'),

    (10, 'Dextrose 5%', 'IV Fluid', 'ml', 0.007, 3900, 4500,
     'Store below 25°C', 4, 'active'),

    (11, 'Sterile Syringe 5mL', 'Consumable', 'box', 14.50, 36, 25,
     'Store in a clean, dry environment', 3, 'active'),

    (12, 'Surgical Gloves Medium', 'Consumable', 'box', 9.80, 32, 30,
     'Store in a cool, dry environment', 1, 'active'),

    (13, 'Heparin 5000 IU/mL', 'Antibiotic', 'vial', 6.25, 55, 45,
     'Store at 2–8°C', 2, 'active'),

    (14, 'Adrenaline 1mg/mL', 'Anaesthetic', 'ampoule', 3.40, 48, 30,
     'Store at 2–8°C and protect from light', 3, 'active');

-- BATCHES
-- 2 expired
-- 3 within 7 days
-- 5 within 30 days
-- Remaining batches 6 months–2 years out

INSERT INTO batches
    (batch_id, medicine_id, batch_number, expiry_date,
     quantity_received, quantity_remaining, received_at)
VALUES
    -- Expired
    (1, 1, 'PCM-2601', '2026-07-15', 300, 0, '2026-05-10T09:00:00'),
    (2, 2, 'IBU-2601', '2026-08-05', 180, 0, '2026-05-18T10:00:00'),

    -- Expiring within 7 days
    (3, 4, 'CEF-2602', '2026-08-30', 60, 18, '2026-06-01T09:30:00'),
    (4, 5, 'PRO-2601', '2026-09-02', 40, 12, '2026-05-20T11:00:00'),
    (5, 7, 'OND-2602', '2026-09-04', 100, 35, '2026-06-05T14:00:00'),

    -- Expiring within 30 days
    (6, 8, 'MET-2601', '2026-09-12', 80, 48, '2026-06-15T08:30:00'),
    (7, 10, 'DEX-2601', '2026-09-20', 5000, 2100, '2026-06-20T10:00:00'),
    (8, 11, 'SYR-2601', '2026-09-25', 30, 18, '2026-06-25T09:00:00'),
    (9, 14, 'ADR-2601', '2026-09-28', 40, 24, '2026-07-01T13:00:00'),
    (10, 3, 'AMX-2602', '2026-09-20', 250, 210, '2026-07-05T10:30:00'),

    -- Long-dated batches
    (11, 1, 'PCM-2602', '2027-04-15', 400, 180, '2026-07-10T09:00:00'),
    (12, 2, 'IBU-2602', '2027-03-20', 250, 95, '2026-07-12T10:00:00'),
    (13, 3, 'AMX-2603', '2027-06-30', 300, 210, '2026-07-15T11:00:00'),
    (14, 4, 'CEF-2603', '2027-05-31', 50, 24, '2026-07-18T09:30:00'),
    (15, 5, 'PRO-2602', '2027-02-28', 30, 16, '2026-07-20T14:00:00'),
    (16, 6, 'LID-2601', '2027-08-31', 100, 60, '2026-07-22T08:00:00'),
    (17, 7, 'OND-2603', '2027-07-15', 80, 40, '2026-07-25T10:30:00'),
    (18, 8, 'MET-2602', '2027-01-31', 100, 48, '2026-07-28T12:00:00'),
    (19, 9, 'SAL-2601', '2028-01-31', 10000, 8500, '2026-07-30T09:00:00'),
    (20, 10, 'DEX-2602', '2027-12-31', 4000, 1800, '2026-08-01T11:00:00'),
    (21, 11, 'SYR-2602', '2027-11-30', 25, 18, '2026-08-03T09:00:00'),
    (22, 12, 'GLV-2601', '2027-09-30', 50, 32, '2026-08-05T10:00:00'),
    (23, 13, 'HEP-2601', '2027-06-30', 70, 55, '2026-08-07T13:00:00'),
    (24, 14, 'ADR-2602', '2027-10-31', 35, 24, '2026-08-10T09:30:00');


-- PURCHASE ORDERS

INSERT INTO purchase_orders
    (po_id, medicine_id, supplier_id, quantity_ordered,
     quantity_received, unit_price, status, created_by, approved_by,
     ai_generated, ai_reasoning, decision_reason, created_at, expected_at)
VALUES
    (1, 1, 1, 500, 0, 0.11, 'ordered', 'pharmacy.manager', 'manager.smith',
     1, 'Projected consumption indicates stock will fall below reorder level.',
     'Approved due to sustained ward demand.', '2026-08-01T09:00:00', '2026-09-06'),

    (2, 2, 1, 300, 0, 0.17, 'approved', 'agent', 'manager.smith',
     1, 'Current stock is below reorder level and recent consumption is elevated.',
     'Approved for routine replenishment.', '2026-08-04T10:00:00', '2026-09-12'),

    (3, 4, 2, 100, 0, 4.60, 'pending_approval', 'agent', NULL,
     1, 'Low stock combined with short expiry window requires replenishment.',
     NULL, '2026-08-12T11:30:00', '2026-08-24'),

    (4, 5, 3, 80, 0, 8.20, 'approved', 'agent', 'manager.jones',
     1, 'Anaesthetic stock is below target and usage has increased.',
     'Approved because theatre usage has increased.', '2026-08-14T08:30:00', '2026-08-28'),

    (5, 7, 2, 150, 0, 0.90, 'draft', 'pharmacy.manager', NULL,
     0, NULL, NULL, '2026-08-16T09:00:00', '2026-08-30'),

    (6, 8, 4, 100, 0, 1.05, 'ordered', 'agent', 'manager.jones',
     1, 'Stock is below reorder level with consistent ward consumption.',
     'Approved based on 30-day consumption history.', '2026-08-18T14:00:00', '2026-09-01'),

    (7, 10, 4, 6000, 0, 0.0065, 'received', 'pharmacy.manager', 'manager.smith',
     0, NULL, 'Received against routine IV fluid replenishment.', '2026-08-05T10:00:00', '2026-08-20'),

    (8, 11, 3, 50, 0, 13.90, 'rejected', 'agent', 'manager.jones',
     1, 'Stock is low but current budget allocation does not support immediate purchase.',
     'Deferred until next budget review.', '2026-08-10T12:00:00', '2026-09-05'),

    (9, 12, 1, 60, 0, 9.20, 'cancelled', 'pharmacy.manager', 'manager.smith',
     0, NULL, 'Cancelled because existing stock is sufficient.', '2026-08-11T15:00:00', '2026-09-10'),

    (10, 13, 2, 100, 0, 5.90, 'pending_approval', 'agent', NULL,
     1, 'Projected demand indicates stock will approach reorder level.',
     NULL, '2026-08-20T09:30:00', '2026-09-08'),

    (11, 14, 3, 70, 0, 3.10, 'approved', 'agent', 'manager.jones',
     1, 'Current adrenaline stock is below reorder level.',
     'Approved due to emergency department demand.', '2026-08-22T10:30:00', '2026-09-03'),

    (12, 9, 4, 10000, 0, 0.0058, 'ordered', 'pharmacy.manager', 'manager.smith',
     0, NULL, 'Routine IV fluid replenishment.', '2026-08-24T11:00:00', '2026-09-07');

-- STOCK MOVEMENTS
-- Spread across approximately the previous 60 days.
-- Quantity is always positive; movement_type determines direction.

INSERT INTO stock_movements
    (movement_id, medicine_id, batch_id, movement_type, quantity,
     reason, performed_by, created_at)
VALUES
    (1, 1, 11, 'receive', 400, 'Routine supplier delivery', 'pharmacy.staff', '2026-06-26T09:15:00'),
    (2, 1, 11, 'issue', 35, 'Ward 1 request', 'pharmacy.staff', '2026-07-12T10:20:00'),
    (3, 1, 11, 'issue', 30, 'Ward 2 request', 'pharmacy.staff', '2026-07-16T11:00:00'),
    (4, 1, 11, 'issue', 30, 'Emergency department request', 'pharmacy.staff', '2026-07-20T14:10:00'),
    (5, 1, 11, 'issue', 35, 'Ward 3 request', 'pharmacy.staff', '2026-07-25T09:45:00'),
    (6, 1, 11, 'issue', 35, 'Ward 1 request', 'pharmacy.staff', '2026-08-01T10:00:00'),
    (7, 1, 11, 'issue', 35, 'Ward 2 request', 'pharmacy.staff', '2026-08-08T13:20:00'),
    (8, 1, 11, 'issue', 20, 'Ward 4 request', 'pharmacy.staff', '2026-08-15T09:30:00'),

    (9, 2, 12, 'receive', 250, 'Routine supplier delivery', 'pharmacy.staff', '2026-06-28T10:15:00'),
    (10, 2, 12, 'issue', 20, 'Ward 1 request', 'pharmacy.staff', '2026-07-15T09:00:00'),
    (11, 2, 12, 'issue', 25, 'Ward 2 request', 'pharmacy.staff', '2026-07-22T10:30:00'),
    (12, 2, 12, 'issue', 30, 'Ward 3 request', 'pharmacy.staff', '2026-07-29T14:00:00'),
    (13, 2, 12, 'issue', 25, 'Emergency department request', 'pharmacy.staff', '2026-08-05T11:30:00'),
    (14, 2, 12, 'issue', 30, 'Ward 1 request', 'pharmacy.staff', '2026-08-12T09:20:00'),
    (15, 2, 12, 'issue', 25, 'Ward 2 request', 'pharmacy.staff', '2026-08-20T13:00:00'),

    (16, 3, 13, 'receive', 300, 'Antibiotic replenishment', 'pharmacy.staff', '2026-06-30T11:15:00'),
    (17, 3, 13, 'issue', 20, 'Ward 2 request', 'pharmacy.staff', '2026-07-18T10:00:00'),
    (18, 3, 13, 'issue', 15, 'Ward 3 request', 'pharmacy.staff', '2026-07-24T09:30:00'),
    (19, 3, 13, 'issue', 25, 'Emergency department request', 'pharmacy.staff', '2026-08-02T11:00:00'),
    (20, 3, 13, 'issue', 30, 'Ward 1 request', 'pharmacy.staff', '2026-08-09T14:15:00'),

    (21, 4, 14, 'receive', 50, 'Routine antibiotic delivery', 'pharmacy.staff', '2026-07-02T09:45:00'),
    (22, 4, 14, 'issue', 2, 'Emergency department request', 'pharmacy.staff', '2026-07-21T12:00:00'),
    (23, 4, 14, 'issue', 10, 'Ward 3 request', 'pharmacy.staff', '2026-07-28T10:30:00'),
    (24, 4, 14, 'issue', 8, 'Emergency department request', 'pharmacy.staff', '2026-08-05T09:45:00'),
    (25, 4, 14, 'issue', 6, 'Ward 2 request', 'pharmacy.staff', '2026-08-14T13:10:00'),

    (26, 5, 15, 'receive', 30, 'Anaesthetic delivery', 'pharmacy.staff', '2026-07-04T14:30:00'),
    (27, 5, 15, 'issue', 4, 'Operating theatre request', 'pharmacy.staff', '2026-07-23T08:30:00'),
    (28, 5, 15, 'issue', 5, 'Operating theatre request', 'pharmacy.staff', '2026-07-31T10:00:00'),
    (29, 5, 15, 'issue', 5, 'Operating theatre request', 'pharmacy.staff', '2026-08-08T08:45:00'),

    (30, 6, 16, 'receive', 100, 'Anaesthetic replenishment', 'pharmacy.staff', '2026-07-06T08:15:00'),
    (31, 6, 16, 'issue', 20, 'Operating theatre request', 'pharmacy.staff', '2026-07-25T09:00:00'),
    (32, 6, 16, 'issue', 8, 'Operating theatre request', 'pharmacy.staff', '2026-08-03T11:15:00'),
    (33, 6, 16, 'issue', 12, 'Emergency department request', 'pharmacy.staff', '2026-08-17T10:30:00'),

    (34, 7, 17, 'receive', 80, 'Antiemetic replenishment', 'pharmacy.staff', '2026-07-09T10:45:00'),
    (35, 7, 17, 'issue', 22, 'Ward 1 request', 'pharmacy.staff', '2026-07-29T09:20:00'),
    (36, 7, 17, 'issue', 10, 'Ward 2 request', 'pharmacy.staff', '2026-08-06T13:00:00'),
    (37, 7, 17, 'issue', 8, 'Ward 3 request', 'pharmacy.staff', '2026-08-16T11:00:00'),

    (38, 8, 18, 'receive', 100, 'Antiemetic replenishment', 'pharmacy.staff', '2026-07-11T12:15:00'),
    (39, 8, 18, 'issue', 20, 'Ward 1 request', 'pharmacy.staff', '2026-08-01T09:15:00'),
    (40, 8, 18, 'issue', 16, 'Ward 2 request', 'pharmacy.staff', '2026-08-10T14:00:00'),
    (41, 8, 18, 'issue', 16, 'Ward 3 request', 'pharmacy.staff', '2026-08-18T10:45:00'),

    (42, 9, 19, 'receive', 10000, 'IV fluid delivery', 'pharmacy.staff', '2026-07-13T09:30:00'),
    (43, 9, 19, 'issue', 400, 'Ward 1 request', 'pharmacy.staff', '2026-08-02T08:30:00'),
    (44, 9, 19, 'issue', 350, 'Ward 2 request', 'pharmacy.staff', '2026-08-06T10:00:00'),
    (45, 9, 19, 'issue', 450, 'Emergency department request', 'pharmacy.staff', '2026-08-11T12:30:00'),
    (46, 9, 19, 'issue', 400, 'Ward 3 request', 'pharmacy.staff', '2026-08-18T09:45:00'),
    (47, 9, 19, 'adjust', 100, 'Inventory reconciliation', 'pharmacy.manager', '2026-08-28T15:00:00'),

    (48, 10, 20, 'receive', 4000, 'IV fluid delivery', 'pharmacy.staff', '2026-07-15T11:15:00'),
    (49, 10, 20, 'issue', 700, 'Ward 1 request', 'pharmacy.staff', '2026-08-04T09:30:00'),
    (50, 10, 20, 'issue', 550, 'Ward 2 request', 'pharmacy.staff', '2026-08-09T10:45:00'),
    (51, 10, 20, 'issue', 950, 'Emergency department request', 'pharmacy.staff', '2026-08-15T13:15:00'),

    (52, 11, 21, 'receive', 25, 'Consumables delivery', 'pharmacy.staff', '2026-07-17T09:15:00'),
    (53, 11, 21, 'issue', 4, 'Ward 1 request', 'pharmacy.staff', '2026-08-07T10:00:00'),
    (54, 11, 21, 'issue', 3, 'Ward 2 request', 'pharmacy.staff', '2026-08-14T11:30:00'),

    (55, 12, 22, 'receive', 50, 'Consumables delivery', 'pharmacy.staff', '2026-07-19T10:15:00'),
    (56, 12, 22, 'issue', 11, 'Ward 1 request', 'pharmacy.staff', '2026-08-09T09:45:00'),
    (57, 12, 22, 'issue', 7, 'Ward 3 request', 'pharmacy.staff', '2026-08-20T14:30:00'),

    (58, 13, 23, 'receive', 70, 'Emergency medication replenishment', 'pharmacy.staff', '2026-07-21T13:15:00'),
    (59, 13, 23, 'issue', 8, 'Emergency department request', 'pharmacy.staff', '2026-08-13T10:30:00'),
    (60, 13, 23, 'issue', 7, 'Emergency department request', 'pharmacy.staff', '2026-08-28T11:15:00');