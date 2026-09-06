# Student 3 database service

This Flask service owns Student 3's SQLite persistence and exposes records over HTTP on **6300**. It is a data-access service: it validates record shapes and performs storage operations, but contains no inventory, FEFO, approval, or AI business logic.

```mermaid
flowchart LR
  UI[Frontend :3300] --> API[Backend :5300]
  API --> DB[Database service :6300]
  DB --> SQLite[(SQLite pharmacy.db)]
```

## Run

From the repository root: `docker compose up -d --build student-3-database`.

Standalone: `cd student-3/database && STUDENT3_DATABASE_PATH=/tmp/pharmacy.db python init_db.py && python app.py`.
Run tests: `python -m unittest discover -s student-3/database/tests -v`.

| Variable | Default | Purpose |
|---|---|---|
| `STUDENT3_DATABASE_PATH` | `pharmacy.db` beside `db.py` | SQLite file path |
| `PORT` | not read by `app.py` | Compose documents `6300`; the current standalone server binds 6300 |

`init_db.py` applies the schema and seeds only an empty database. The seed contains at least 10 records in every required table; transactional tables have more records than the staff and supplier masters.

## Tables

| Table | Fields | Foreign keys |
|---|---|---|
| `staff` | `staff_id INTEGER PK`, `name TEXT`, `role TEXT`, `notes TEXT`, `created_at TEXT`, `updated_at TEXT` | — |
| `suppliers` | `supplier_id INTEGER PK`, `name TEXT`, `contact_email TEXT`, `phone TEXT`, `lead_time_days INTEGER`, `status TEXT` | — |
| `medicines` | `medicine_id INTEGER PK`, `name/category/unit TEXT`, `unit_price REAL`, `stock_quantity/reorder_level INTEGER`, `storage_instructions TEXT`, `supplier_id INTEGER`, `status TEXT` | `supplier_id → suppliers` |
| `batches` | `batch_id INTEGER PK`, `medicine_id INTEGER`, `batch_number TEXT`, `expiry_date/received_at TEXT`, `quantity_received/quantity_remaining INTEGER` | `medicine_id → medicines`; unique `(medicine_id, batch_number)` |
| `purchase_orders` | `po_id INTEGER PK`, `medicine_id/supplier_id INTEGER`, quantities, `unit_price REAL`, `status`, ownership/AI/reason fields, `created_at/expected_at TEXT` | medicine and supplier IDs |
| `stock_movements` | `movement_id INTEGER PK`, `medicine_id INTEGER`, optional `batch_id`, `movement_type TEXT`, `quantity INTEGER`, `reason/performed_by/created_at TEXT` | medicine and batch IDs |

SQLite has no native BOOLEAN or DATE type, so flags are stored as constrained integers and dates/times as ISO text. `PRAGMA foreign_keys=ON` is set for every connection. Medicine and supplier deletion is a status update; stock movements are append-only.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | health response |
| GET | `/staff`, `/staff/<id>` | list/filter staff or retrieve one |
| GET/POST | `/medicines` | list/create medicines |
| GET/PUT/DELETE | `/medicines/<id>` | retrieve/update/discontinue medicine |
| GET | `/medicines/<id>/batches` | medicine batches |
| GET/POST | `/batches` | list/filter or create batches |
| GET/PUT | `/batches/<id>` | retrieve/update remaining quantity |
| GET/POST | `/suppliers` | list/create suppliers |
| GET/PUT/DELETE | `/suppliers/<id>` | retrieve/update/discontinue supplier |
| GET/POST | `/purchase_orders` | list/create orders |
| GET/PUT | `/purchase_orders/<id>` | retrieve/update allowed order fields |
| PATCH | `/purchase_orders/<id>/status` | update order status |
| GET/POST | `/stock_movements` | list/filter or append ledger movement |
| PUT/DELETE | `/stock_movements/<id>` | always `405`: append-only ledger |

## Known issues and limitations

The service does not provide staff write endpoints. It does not automatically reconcile `medicines.stock_quantity` with batches; the backend performs that orchestration. SQLite is local-file storage and is not suitable for multiple independent writers without further deployment controls.
