# Student 5 — Database Microservice

**Feature:** Staff & Shift Management
**Layer:** Database microservice only (no API routes, no frontend, no authentication)

Implements the approved design in
[`docs/architecture/student-5-database-design.md`](../../docs/architecture/student-5-database-design.md),
following prompt artefact `S5-DB-001` in
[`docs/prompts/student-5/database-development.md`](../../docs/prompts/student-5/database-development.md).

## Files

| File | Purpose |
|------|---------|
| `schema.sql` | DDL for the three entities, indexes, and `updated_at` triggers |
| `db.py` | Connection handling (path resolution, `sqlite3.Row`, foreign key pragma) |
| `models.py` | Dataclass definitions mirroring each table |
| `repository.py` | CRUD data-access functions for the backend/API microservice to call |
| `seed_data.py` | Seed records and the `seed()` routine |
| `init_db.py` | Database initialisation CLI |
| `verify_db.py` | Validation script mapped to the approved validation criteria |
| `service.py` | HTTP data service exposing the repository to the backend microservice |

## Entities

```
STAFF ---1:M--- SHIFT_ASSIGNMENT ---M:1--- SHIFT
```

- **staff** — workforce records (`staff_id` PK)
- **shift** — planned shifts (`shift_id` PK)
- **shift_assignment** — resolves the many-to-many relationship between staff
  and shifts (`assignment_id` PK, foreign keys to both parents)

## Requirements

Python 3.x only. The service uses the standard library `sqlite3` module, so
there are no third-party dependencies to install.

## Initialise the database

Run from this directory (`student-5/database/`):

```bash
python3 init_db.py
```

Creates `staff_shift.db` with the schema and seed data. Seeding is skipped if
the database already holds records.

Rebuild from scratch:

```bash
python3 init_db.py --reset
```

Create the schema without seed data:

```bash
python3 init_db.py --no-seed
```

## Verify the database

```bash
python3 verify_db.py
```

Checks table existence, primary keys, foreign keys, minimum record counts,
relationship integrity, and that the M:N relationship resolves in both
directions. Exits non-zero if any check fails.

## Seed data

| Table | Records | Minimum required |
|-------|---------|------------------|
| `staff` | 12 | 10 |
| `shift` | 13 | 10 |
| `shift_assignment` | 17 | 10 |

Two staff members are deliberately left unassigned (one `On Leave`, one
`Unavailable`) so availability filtering has realistic data to work against.

## Configuration

The database location can be overridden with an environment variable, which is
how the path will be set inside Docker later:

```bash
STUDENT5_DATABASE_PATH=/data/staff_shift.db python3 init_db.py
```

Both scripts also accept `--path`.

## Running as a service

The backend/API microservice reaches this data over HTTP rather than opening
the SQLite file, so the data service must be running:

```bash
cd student-5/database && python3 service.py
```

Serves on port 6500 (override with `DATABASE_SERVICE_PORT`). Check it with:

```bash
curl -s http://127.0.0.1:6500/health
```

## Notes

- `staff_shift.db` is a local build artefact and is ignored by the repository
  `.gitignore`. It is recreated by running `init_db.py`.
- Foreign key enforcement is enabled per connection in `db.py`; SQLite disables
  it by default, which would otherwise ignore the declared relationships.
- This microservice owns only Staff & Shift Management data and holds no
  reference to any other student's database.
