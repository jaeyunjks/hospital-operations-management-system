PRAGMA foreign_keys = ON;

-- 14 suppliers: the original four are retained so existing demonstrations
-- continue to show familiar names. Suppliers 13 and 14 are discontinued.
INSERT INTO suppliers (supplier_id, name, contact_email, phone, lead_time_days, status) VALUES
    (1, 'MedSupply Australia', 'orders@medsupply.example.com', '02-9000-1001', 5, 'active'),
    (2, 'HealthCore Pharmaceuticals', 'sales@healthcore.example.com', '03-9000-1002', 8, 'active'),
    (3, 'Pacific Medical Distributors', 'orders@pacificmedical.example.com', '07-9000-1003', 12, 'active'),
    (4, 'Southern Clinical Supplies', 'contact@southernclinical.example.com', '08-9000-1004', 6, 'active'),
    (5, 'MetroMed Wholesale', 'orders@metromed.example.com', '02-9000-1005', 2, 'active'),
    (6, 'Northern Therapeutics', 'supply@northerntherapeutics.example.com', '03-9000-1006', 4, 'active'),
    (7, 'WestCare Medical', 'orders@westcare.example.com', '08-9000-1007', 7, 'active'),
    (8, 'Central Hospital Supply', 'sales@centralhospital.example.com', '07-9000-1008', 9, 'active'),
    (9, 'Apex Clinical Logistics', 'orders@apexclinical.example.com', '02-9000-1009', 11, 'active'),
    (10, 'Vitality Pharma', 'supply@vitalitypharma.example.com', '03-9000-1010', 14, 'active'),
    (11, 'Coastal Sterile Products', 'orders@coastalsterile.example.com', '08-9000-1011', 17, 'active'),
    (12, 'National Specialty Medicines', 'orders@nationalspecialty.example.com', '07-9000-1012', 21, 'active'),
    (13, 'Legacy Medical Imports', 'service@legacymedical.example.com', '02-9000-1013', 15, 'discontinued'),
    (14, 'Retired Clinical Supply', 'support@retiredclinical.example.com', '03-9000-1014', 18, 'discontinued');

-- The original 14 medicines are retained for screenshot continuity.
INSERT INTO medicines (medicine_id, name, category, unit, unit_price, stock_quantity, reorder_level, storage_instructions, supplier_id, status) VALUES
    (1, 'Paracetamol 500mg', 'Analgesic', 'tablet', 0.12, 0, 200, 'Store below 25°C in a dry place', 1, 'active'),
    (2, 'Ibuprofen 400mg', 'Analgesic', 'tablet', 0.18, 0, 120, 'Store below 25°C in a dry place', 1, 'active'),
    (3, 'Amoxicillin 500mg', 'Antibiotic', 'capsule', 0.42, 0, 150, 'Store below 25°C and protect from moisture', 2, 'active'),
    (4, 'Ceftriaxone 1g', 'Antibiotic', 'vial', 4.80, 0, 50, 'Store below 25°C', 2, 'active'),
    (5, 'Propofol 10mg/mL', 'Anaesthetic', 'vial', 8.50, 0, 35, 'Store at 2–8°C. Protect from light.', 3, 'active'),
    (6, 'Lidocaine 2%', 'Anaesthetic', 'vial', 2.75, 0, 40, 'Store below 25°C', 3, 'active'),
    (7, 'Ondansetron 4mg', 'Antiemetic', 'tablet', 0.95, 0, 100, 'Store below 25°C', 2, 'active'),
    (8, 'Metoclopramide 10mg', 'Antiemetic', 'ampoule', 1.15, 0, 60, 'Store below 25°C', 4, 'active'),
    (9, 'Sodium Chloride 0.9%', 'IV Fluid', 'mL', 0.006, 0, 5000, 'Store below 25°C', 4, 'active'),
    (10, 'Dextrose 5%', 'IV Fluid', 'mL', 0.007, 0, 4500, 'Store below 25°C', 4, 'active'),
    (11, 'Sterile Syringe 5mL', 'Consumable', 'box', 14.50, 0, 25, 'Store in a clean, dry environment', 3, 'active'),
    (12, 'Surgical Gloves Medium', 'Consumable', 'box', 9.80, 0, 30, 'Store in a cool, dry environment', 1, 'active'),
    (13, 'Heparin 5000 IU/mL', 'Antibiotic', 'vial', 6.25, 0, 45, 'Store at 2–8°C', 2, 'active'),
    (14, 'Adrenaline 1mg/mL', 'Anaesthetic', 'ampoule', 3.40, 0, 30, 'Store at 2–8°C and protect from light', 3, 'active');

-- 126 additional medicines, with eight discontinued legacy items (133–140).
INSERT INTO medicines (medicine_id, name, category, unit, unit_price, stock_quantity, reorder_level, storage_instructions, supplier_id, status)
WITH RECURSIVE numbers(n) AS (SELECT 15 UNION ALL SELECT n + 1 FROM numbers WHERE n < 140)
SELECT n,
    CASE n % 12
      WHEN 0 THEN 'Oseltamivir 75mg'
      WHEN 1 THEN 'Metoprolol 25mg'
      WHEN 2 THEN 'Salbutamol 2.5mg'
      WHEN 3 THEN 'Sterile Dressing Pack'
      WHEN 4 THEN 'Acyclovir 400mg'
      WHEN 5 THEN 'Furosemide 40mg'
      WHEN 6 THEN 'Omeprazole 20mg'
      WHEN 7 THEN 'Ciprofloxacin 500mg'
      WHEN 8 THEN 'Insulin Regular 100 IU/mL'
      WHEN 9 THEN 'Azithromycin 250mg'
      WHEN 10 THEN 'Morphine 10mg/mL'
      ELSE 'Normal Saline Flush 10mL'
    END || ' (' || printf('%03d', n) || ')',
    CASE n % 12
      WHEN 0 THEN 'Antiviral' WHEN 1 THEN 'Cardiac' WHEN 2 THEN 'Respiratory'
      WHEN 3 THEN 'Dressing' WHEN 4 THEN 'Antiviral' WHEN 5 THEN 'Cardiac'
      WHEN 6 THEN 'Gastrointestinal' WHEN 7 THEN 'Antibiotic' WHEN 8 THEN 'Endocrine'
      WHEN 9 THEN 'Antibiotic' WHEN 10 THEN 'Analgesic' ELSE 'Consumable'
    END,
    CASE n % 6 WHEN 0 THEN 'capsule' WHEN 1 THEN 'tablet' WHEN 2 THEN 'ampoule' WHEN 3 THEN 'box' WHEN 4 THEN 'vial' ELSE 'mL' END,
    round(CASE n % 12 WHEN 3 THEN 18.50 WHEN 8 THEN 32.00 WHEN 10 THEN 14.75 WHEN 0 THEN 5.80 ELSE 0.20 + (n * 0.31) END, 2),
    0,
    CASE WHEN n <= 25 THEN 60 ELSE 20 + ((n % 5) * 10) END,
    CASE n % 4 WHEN 0 THEN 'Store at 2–8°C and protect from light.' WHEN 1 THEN 'Store below 25°C in a dry place.' WHEN 2 THEN 'Keep in original packaging and protect from moisture.' ELSE 'Store in a clean, dry clinical store.' END,
    CASE WHEN n >= 133 THEN 13 + ((n - 133) % 2) ELSE 1 + ((n - 15) % 12) END,
    CASE WHEN n >= 133 THEN 'discontinued' ELSE 'active' END
FROM numbers;

-- 280 batches. Eight are expired; ten expire in seven days; 25 within 30;
-- 40 within 90; the balance is six months to two years away. Medicines 1–10
-- deliberately have three differently dated batches for FEFO demonstrations.
INSERT INTO batches (batch_id, medicine_id, batch_number, expiry_date, quantity_received, quantity_remaining, received_at)
WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM numbers WHERE n < 280),
mapped AS (
  SELECT n,
    CASE WHEN n <= 140 THEN n WHEN n <= 270 THEN n - 140 ELSE n - 270 END AS medicine_id
  FROM numbers
)
SELECT n, medicine_id, 'PHM-' || printf('%05d', 26000 + n),
  CASE
    WHEN n <= 8 THEN date('now', '-' || (9 - n) || ' days')
    WHEN n <= 18 THEN date('now', '+' || (1 + ((n - 9) % 7)) || ' days')
    WHEN n <= 43 THEN date('now', '+' || (8 + ((n - 19) % 23)) || ' days')
    WHEN n <= 83 THEN date('now', '+' || (33 + (n - 44)) || ' days')
    ELSE date('now', '+' || (180 + ((n - 84) * 3)) || ' days')
  END,
  CASE WHEN medicine_id IN (9, 10) THEN 5000 + (n * 50) ELSE 320 + ((n % 8) * 40) END,
  CASE
    WHEN n <= 8 THEN 0
    WHEN medicine_id <= 25 THEN 10
    WHEN medicine_id IN (9, 10) THEN 2600 + ((n % 4) * 500)
    WHEN n % 29 = 0 THEN 0
    ELSE 50 + ((n % 7) * 30)
  END,
  datetime('now', '-' || (70 + (n % 160)) || ' days', '+' || (n % 8) || ' hours')
FROM mapped;

-- 70 purchase orders span every workflow state. The first 20 are AI-generated
-- recommendations; pending-approval orders exceed the required five records.
INSERT INTO purchase_orders (po_id, medicine_id, supplier_id, quantity_ordered, quantity_received, unit_price, status, created_by, approved_by, ai_generated, ai_reasoning, decision_reason, created_at, expected_at)
WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM numbers WHERE n < 70)
SELECT n, n, 1 + ((n - 1) % 12), 50 + (n * 10),
  CASE WHEN n % 7 = 4 THEN 50 + (n * 10) WHEN n % 7 = 3 AND n % 3 = 0 THEN 20 + n ELSE 0 END,
  round(0.15 + (n * 0.37), 2),
  CASE n % 7 WHEN 0 THEN 'draft' WHEN 1 THEN 'pending_approval' WHEN 2 THEN 'approved' WHEN 3 THEN 'ordered' WHEN 4 THEN 'received' WHEN 5 THEN 'rejected' ELSE 'cancelled' END,
  CASE WHEN n <= 20 THEN 'Reorder assistant' ELSE 'Olivia Martin' END,
  CASE WHEN n % 7 IN (2, 3, 4, 5, 6) THEN 'James Wilson' ELSE NULL END,
  CASE WHEN n <= 20 THEN 1 ELSE 0 END,
  CASE WHEN n <= 20 THEN 'Projected consumption and current stock indicate replenishment is required.' ELSE NULL END,
  CASE n % 7 WHEN 2 THEN 'Approved after pharmacy stock review.' WHEN 4 THEN 'Delivery reconciled against supplier invoice.' WHEN 5 THEN 'Rejected after clinical demand review.' WHEN 6 THEN 'Cancelled because stock recovered.' ELSE NULL END,
  datetime('now', '-' || (n % 55) || ' days', '+' || (n % 9) || ' hours'),
  date('now', '+' || ((n % 45) - 15) || ' days')
FROM numbers;

-- 700 append-only ledger events over the last 60 days. The first 280 are
-- receive events tied directly to every batch arrival. The remainder is mostly
-- ward/theatre/emergency issues, with controlled adjustment and waste variety.
INSERT INTO stock_movements (movement_id, medicine_id, batch_id, movement_type, quantity, reason, performed_by, created_at)
WITH RECURSIVE numbers(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM numbers WHERE n < 700)
SELECT n, b.medicine_id, b.batch_id,
  CASE WHEN n <= 280 THEN 'receive' WHEN n % 14 = 0 THEN 'adjust' WHEN n % 14 = 13 THEN 'waste' ELSE 'issue' END,
  CASE WHEN n <= 280 THEN b.quantity_received WHEN b.medicine_id IN (9, 10) THEN 250 + ((n % 6) * 100) WHEN b.medicine_id % 9 = 0 THEN 1 + (n % 4) ELSE 4 + ((n % 8) * 3) END,
  CASE WHEN n <= 280 THEN 'Supplier delivery received' WHEN n % 14 = 0 THEN 'Cycle count adjustment' WHEN n % 14 = 13 THEN CASE WHEN b.expiry_date < date('now') THEN 'Expired' ELSE 'Damaged stock disposal' END WHEN n % 5 = 0 THEN 'Theatre request' WHEN n % 5 = 1 THEN 'Emergency request' WHEN n % 5 = 2 THEN 'Ward 1 request' WHEN n % 5 = 3 THEN 'Ward 3 request' ELSE 'Ward 5 request' END,
  CASE n % 12 WHEN 0 THEN 'Olivia Martin' WHEN 1 THEN 'James Wilson' WHEN 2 THEN 'Amara Okafor' WHEN 3 THEN 'Daniel Reyes' WHEN 4 THEN 'Priya Nandakumar' WHEN 5 THEN 'Liam O''Sullivan' WHEN 6 THEN 'Mei Lin Tan' WHEN 7 THEN 'Grace Mwangi' WHEN 8 THEN 'Hassan Al-Rashid' WHEN 9 THEN 'Sofia Petrova' WHEN 10 THEN 'Ethan Brooks' ELSE 'Rina Kobayashi' END,
  datetime('now', '-' || ((n - 1) % 60) || ' days', '+' || (7 + (n % 11)) || ' hours', '+' || ((n * 7) % 59) || ' minutes')
FROM numbers JOIN batches b ON b.batch_id = 1 + ((n - 1) % 280);

-- Exactly 12 demo identities: two managers and ten pharmacy staff.
INSERT INTO staff (staff_id, name, role, notes) VALUES
    (1, 'Olivia Martin', 'manager', 'Pharmacy operations manager.'),
    (2, 'James Wilson', 'manager', 'Assistant pharmacy manager.'),
    (3, 'Amara Okafor', 'staff', 'Pharmacy technician.'),
    (4, 'Daniel Reyes', 'staff', 'Dispensary staff member.'),
    (5, 'Priya Nandakumar', 'staff', 'Inventory control staff member.'),
    (6, 'Liam O''Sullivan', 'staff', 'Pharmacy technician.'),
    (7, 'Mei Lin Tan', 'staff', 'Dispensary staff member.'),
    (8, 'Grace Mwangi', 'staff', 'Inventory control staff member.'),
    (9, 'Hassan Al-Rashid', 'staff', 'Pharmacy technician.'),
    (10, 'Sofia Petrova', 'staff', 'Dispensary staff member.'),
    (11, 'Ethan Brooks', 'staff', 'Inventory control staff member.'),
    (12, 'Rina Kobayashi', 'staff', 'Dispensary staff member.');

-- Inventory is derived from non-expired batch balances; do not hand-edit it.
UPDATE medicines
SET stock_quantity = COALESCE((
  SELECT SUM(quantity_remaining)
  FROM batches
  WHERE batches.medicine_id = medicines.medicine_id
    AND batches.expiry_date >= date('now')
), 0);
