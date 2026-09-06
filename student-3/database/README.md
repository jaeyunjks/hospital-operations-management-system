# Student 3 Database Service

Data-access service for Pharmacy & Medication Inventory Management. It exposes pharmacy SQLite data through HTTP and contains no inventory, approval, AI, or workflow business logic. **Port: 6300.**

## Role in the architecture

```mermaid
flowchart LR
  F[Frontend :3300] -->|HTTP| B[Backend :5300]
  B -->|HTTP| D[Database service :6300]
  D --> S[(SQLite pharmacy.db)]
```

The frontend never calls 6300 and never opens the `.db` file. The backend never opens the `.db` file: it calls this service over HTTP. This service does not call the frontend, backend, Ollama, or other student services.

## Tech stack

| Component | Version / use |
|---|---|
| Python | Python 3; Docker image uses 3.12-slim |
| Flask | 3.0.3 |
| SQLite | Python standard-library `sqlite3` |
| HTTP | Flask JSON API |

## Folder structure

```text
database/
├── app.py                  # Flask CRUD and health endpoints
├── db.py                   # SQLite connection, schema, and seed helpers
├── init_db.py              # Idempotent initialise and validation command
├── schema.sql              # Six table definitions and staff trigger
├── seed_data.sql           # Demonstration records
├── requirements.txt        # Flask dependency
├── Dockerfile              # Port-6300 image
├── Dockerfile.dockerignore # Root-context Docker exclusions
├── tests/
│   └── test_database_service.py # Temporary-database service tests
└── README.md               # This document
```

## Running locally

Run this from the repository root. Start database, backend, and frontend in three terminals, in that order: **database → backend → frontend**.

```bash
cd student-3/database && pip install -r requirements.txt && python3 init_db.py --check && python3 app.py
```

`init_db.py --check` applies the schema, seeds only when `medicines` is empty, then checks all six tables, the 10-record minimum, and seeded manager/staff role counts. Local data is `student-3/database/pharmacy.db`.

## Running with Docker

```bash
docker compose up -d --build
docker compose logs -f student-3-database
docker compose stop student-3-database
```

Compose mounts `student3-database-data` at `/data`; the service uses `/data/pharmacy.db`. The named volume persists across restarts. Startup reapplies schema but skips seed data when an existing volume has medicines.

## Configuration

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `PORT` | `6300` | No | HTTP listen port. |
| `STUDENT3_DATABASE_PATH` | `student-3/database/pharmacy.db` | No | SQLite location; Compose sets `/data/pharmacy.db`. |

## Schema and SQLite choices

SQLite has no native `BOOLEAN` or `DATE` type here. Boolean values use constrained `INTEGER` (`0`/`1`); dates and timestamps are ISO-8601 `TEXT`. Every connection executes `PRAGMA foreign_keys = ON`, because SQLite enables FK enforcement per connection. Medicine deletion is a status change, preserving the row. `stock_movements` is append-only by API policy; its update/delete endpoint returns 405.

| Table | Fields, types, constraints, and foreign keys |
|---|---|
| `staff` | `staff_id INTEGER PRIMARY KEY AUTOINCREMENT`; `name TEXT NOT NULL`; `role TEXT NOT NULL`; `notes TEXT`; `created_at TEXT NOT NULL DEFAULT datetime('now')`; `updated_at TEXT NOT NULL DEFAULT datetime('now')`. An update trigger refreshes `updated_at`. |
| `suppliers` | `supplier_id INTEGER PRIMARY KEY AUTOINCREMENT`; `name TEXT NOT NULL`; `contact_email TEXT`; `phone TEXT`; `lead_time_days INTEGER NOT NULL`; `status TEXT NOT NULL DEFAULT 'active' CHECK ('active','discontinued')`. |
| `medicines` | `medicine_id INTEGER PRIMARY KEY AUTOINCREMENT`; `name TEXT NOT NULL`; `category TEXT NOT NULL`; `unit TEXT NOT NULL`; `unit_price REAL NOT NULL CHECK >= 0`; `stock_quantity INTEGER NOT NULL DEFAULT 0`; `reorder_level INTEGER NOT NULL`; `storage_instructions TEXT`; `supplier_id INTEGER` FK → `suppliers.supplier_id`; `status TEXT NOT NULL DEFAULT 'active' CHECK ('active','discontinued')`. |
| `batches` | `batch_id INTEGER PRIMARY KEY AUTOINCREMENT`; `medicine_id INTEGER NOT NULL` FK → `medicines.medicine_id`; `batch_number TEXT NOT NULL`; `expiry_date TEXT NOT NULL`; `quantity_received INTEGER NOT NULL`; `quantity_remaining INTEGER NOT NULL CHECK >= 0`; `received_at TEXT NOT NULL`; `UNIQUE(medicine_id, batch_number)`. |
| `purchase_orders` | `po_id INTEGER PRIMARY KEY`; `medicine_id INTEGER NOT NULL` FK → `medicines.medicine_id`; `supplier_id INTEGER NOT NULL` FK → `suppliers.supplier_id`; `quantity_ordered INTEGER NOT NULL CHECK > 0`; `quantity_received INTEGER NOT NULL DEFAULT 0`; `unit_price REAL`; `status TEXT NOT NULL DEFAULT 'draft'` constrained to `draft`, `pending_approval`, `approved`, `ordered`, `received`, `rejected`, `cancelled`; `created_by TEXT`; `approved_by TEXT`; `ai_generated INTEGER NOT NULL DEFAULT 0 CHECK (0,1)`; `ai_reasoning TEXT`; `decision_reason TEXT`; `created_at TEXT NOT NULL`; `expected_at TEXT`. |
| `stock_movements` | `movement_id INTEGER PRIMARY KEY`; `medicine_id INTEGER NOT NULL` FK → `medicines.medicine_id`; `batch_id INTEGER` FK → `batches.batch_id`; `movement_type TEXT NOT NULL` constrained to `receive`, `issue`, `adjust`, `waste`; `quantity INTEGER NOT NULL`; `reason TEXT`; `performed_by TEXT`; `created_at TEXT NOT NULL`. |

Seed data contains 14 suppliers, 140 medicines, 175 batches, 12 purchase orders, 20 stock movements, and 12 staff records. Every table meets the Student-3 minimum of 10 records.

## API reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service health and row counts. |
| GET | `/staff` | List staff. |
| GET | `/staff/{staff_id}` | Get staff by ID. |
| GET, POST | `/medicines` | List or create medicines. |
| GET, PUT, DELETE | `/medicines/{medicine_id}` | Read, update, or soft-discontinue medicine. |
| GET | `/medicines/{medicine_id}/batches` | List batches for medicine. |
| GET, POST | `/batches` | List or create batches. |
| GET, PUT | `/batches/{batch_id}` | Read or update batch. |
| GET, POST | `/suppliers` | List or create suppliers. |
| GET, PUT, DELETE | `/suppliers/{supplier_id}` | Read, update, or discontinue supplier. |
| GET, POST | `/purchase_orders` | List or create orders. |
| GET, PUT | `/purchase_orders/{po_id}` | Read or edit order. |
| PATCH | `/purchase_orders/{po_id}/status` | Change order status and approval metadata. |
| GET, POST | `/stock_movements` | List or append movement. |
| PUT, DELETE | `/stock_movements/{movement_id}` | Always 405: append-only history. |

Observed health request and response:

```http
GET /health
```

```json
{"status":"healthy"}
```

```http
POST /batches
Content-Type: application/json

{"medicine_id":1,"batch_number":"LOT-100","expiry_date":"2027-06-01","quantity_received":50,"quantity_remaining":50,"received_at":"2026-09-06"}
```

The response is the created batch JSON object, including its database-assigned
`batch_id` and the submitted batch fields. Values depend on the database state.

## Running the tests

```bash
cd student-3/database && python3 -m unittest discover -s tests -v
```

The suite uses a temporary SQLite file, never `/data/pharmacy.db`, and covers schema/seeds, CRUD, FKs, medicine soft delete, and append-only movements. The current suite passes **4 tests**.

## Troubleshooting

| Problem | Fix |
|---|---|
| Missing tables or seed-count error | Run `python3 init_db.py --check`. |
| Docker data disappears | Confirm `student3-database-data` is attached at `/data`. |
| Invalid foreign key succeeds in an ad-hoc script | Enable `PRAGMA foreign_keys = ON` for that connection. |
| Append-only 405 response | Create a corrective movement; do not alter history. |

## Known issues and limitations

- It intentionally has no workflow validation or authorisation.
- SQLite has single-writer limitations that suit a demonstration, not high concurrency.
- Date/time values are text; callers must supply valid ISO-8601 values.
