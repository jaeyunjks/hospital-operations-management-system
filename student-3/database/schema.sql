PRAGMA foreign_keys = ON;

-- Supplier master record for pharmaceutical vendors.
CREATE TABLE suppliers (
    supplier_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    contact_email TEXT,
    phone TEXT,
    lead_time_days INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'discontinued'))
);

-- medicines.stock_quantity intentionally duplicates the quantity that could be calculated
-- from batches. It represents total stock across non-expired batches and must
-- be updated whenever stock is received, issued, or written off.
CREATE TABLE medicines (
    medicine_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    stock_quantity INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL,
    storage_instructions TEXT,
    supplier_id INTEGER,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'discontinued')),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
);

-- Batch records capture per-lot expiry and remaining quantity for traceability.
CREATE TABLE batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    medicine_id INTEGER NOT NULL,
    batch_number TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    quantity_received INTEGER NOT NULL,
    quantity_remaining INTEGER NOT NULL CHECK(quantity_remaining >= 0),
    received_at TEXT NOT NULL,
    FOREIGN KEY (medicine_id) REFERENCES medicines(medicine_id),
    UNIQUE (medicine_id, batch_number)
);

-- Purchase orders track expected replenishment and approval states.
CREATE TABLE purchase_orders (
    po_id INTEGER PRIMARY KEY,
    medicine_id INTEGER NOT NULL,
    supplier_id INTEGER NOT NULL,
    quantity_ordered INTEGER NOT NULL CHECK(quantity_ordered > 0),
    quantity_received INTEGER NOT NULL DEFAULT 0,
    unit_price REAL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN (
            'draft',
            'pending_approval',
            'approved',
            'ordered',
            'received',
            'rejected',
            'cancelled'
        )),
    created_by TEXT,
    approved_by TEXT,
    ai_generated INTEGER NOT NULL DEFAULT 0
        CHECK (ai_generated IN (0, 1)),
    ai_reasoning TEXT,
    decision_reason TEXT,
    created_at TEXT NOT NULL,
    expected_at TEXT,
    FOREIGN KEY (medicine_id) REFERENCES medicines(medicine_id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
);

-- stock_movements is append-only by application design.
-- The API layer enforces write rules; this schema intentionally avoids update/delete triggers.
CREATE TABLE stock_movements (
    movement_id INTEGER PRIMARY KEY,
    medicine_id INTEGER NOT NULL,
    batch_id INTEGER,
    movement_type TEXT NOT NULL
        CHECK (movement_type IN ('receive', 'issue', 'adjust', 'waste')),
    quantity INTEGER NOT NULL,
    reason TEXT,
    performed_by TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (medicine_id) REFERENCES medicines(medicine_id),
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id)
);
