# student-4 / database

Database microservice for **Room, Bed & Operating Theatre Management**.
Owner: Jisoo Jung (Asher).

## Files

| File | Purpose |
|------|---------|
| `schema.sql` | Table definitions, constraints and indexes |
| `seed_data.sql` | Seed records (10+ per table, as the specification requires) |
| `init_db.py` | Builds `rooms.db` from the two SQL files and validates it |

`rooms.db` itself is **not** committed — it is generated. The repository
`.gitignore` already excludes `*.db`.

## Building the database

From this directory:

```bash
python init_db.py --check
```

Expected output:

```
Created .../rooms.db
  room_types            10 records
  rooms                 14 records
  beds                  25 records
  room_arrangements     14 records

Consistency check passed.
```

Re-running rebuilds the database from scratch — `schema.sql` drops every
table first.

## Tables

| Table | Holds |
|-------|-------|
| `room_types` | The 10 room types, each mapped to a care category |
| `rooms` | Physical rooms, including the three operating theatres |
| `beds` | Allocatable slots within a room; an operating table is a bed |
| `room_arrangements` | Inpatient bed stays **and** operating theatre sessions |

### Care categories

Every room type belongs to one of `Surgical`, `Short-term` or `Long-term`.
Placement narrows candidate beds to the matching category first, which keeps
patients with similar care needs together and gives the AI suggestion
endpoint a much smaller, more reliable set to rank.

### Why arrangements hold both stays and surgery

An operating theatre session and an inpatient stay are both "a patient
occupying a slot between two times". Keeping them in one table means the
double-booking conflict check is written once and covers both.
`purpose` distinguishes them; `procedure_name` and `surgeon_name` are only
populated for surgery.

## Deletion policy

Nothing is hard deleted.

- Rooms and beds are retired by setting `status` to `Out of Service` /
  `maintenance`, so historical arrangements remain valid.
- Room types can only be deleted when no room references them.
- Arrangements are cancelled by setting `status = 'Cancelled'`, preserving a
  complete audit trail of every bed stay and theatre session.

## Cross-service note

`room_arrangements.patient_id` refers to the Patient & Admission service
(Student 1). It is deliberately **not** a foreign key — that table lives in a
separate database microservice, so the constraint cannot be declared here.
The seeded values (1001–1014) are placeholders and must be reconciled with
Student 1's patient records before integration.

## Consistency guarantees

`init_db.py --check` verifies that the seed data holds these invariants,
which the API layer must also maintain:

- every table has at least 10 records
- no foreign key violations
- a bed marked `occupied` has exactly one `In Progress` arrangement
- an `In Progress` arrangement always sits on an `occupied` bed
- no two active arrangements overlap in time on the same bed
