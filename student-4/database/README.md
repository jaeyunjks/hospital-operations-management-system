# student-4 / database

Database microservice for **Room & Bed Management**. Owner: Jisoo Jung (Asher).

Runs on **port 6400**. This is the only process that opens `rooms.db`. The
backend and every other team service reach the data through this HTTP API, which
is what the HOMS collaboration rules require: *"services never read another
service's database directly."*

## Files

| File | Purpose |
|------|---------|
| `schema.sql` | Table definitions, constraints and indexes |
| `seed_data.sql` | Seed records (10+ per table, as the specification requires) |
| `init_db.py` | Builds `rooms.db` from the two SQL files and validates it |
| `db.py` | Connection helpers, one connection per request |
| `app.py` | The Flask API |
| `Dockerfile` | Container build; the database is seeded at image build time |

`rooms.db` is **not** committed — it is generated, and `.gitignore` excludes `*.db`.

## Building and running

```bash
python init_db.py --check     # build and validate
python app.py                 # serve on 6400
```

Expected build output:

```
Created .../rooms.db
  room_types            10 records
  rooms                 14 records
  beds                  25 records
  room_arrangements     16 records
  shortage_cases        10 records

Consistency check passed.
```

Re-running rebuilds from scratch — `schema.sql` drops every table first.

## Tables

| Table | Holds |
|-------|-------|
| `room_types` | The 10 room types, each mapped to a care category |
| `rooms` | Physical rooms, including the three operating theatres |
| `beds` | Allocatable slots within a room; an operating table is a bed |
| `room_arrangements` | Inpatient stays **and** theatre sessions, including transfers |
| `shortage_cases` | The bed shortage workflow |

### Care categories

Every room type belongs to `Surgical`, `Short-term` or `Long-term`. Placement
narrows candidates to the matching category first, which keeps patients with
similar care needs together and gives the AI suggestion endpoint a small,
reliable set to rank rather than every bed in the hospital.

### Why stays and surgery share one table

An operating theatre session and an inpatient stay are both "a patient occupying
a slot between two times". One table means the double-booking conflict check is
written once and covers both. `purpose` distinguishes them; `procedure_name` and
`surgeon_name` are populated only for surgery.

### Bed states

`available → reserved → occupied` implements the reserve/allocate/occupy sequence
the shortage workflow needs (Architecture v2.2 §5.2). `reserved` means a
coordinator has committed the bed to a waiting patient but no arrangement exists
yet, so it cannot be handed to anyone else. `maintenance` is the retired state.

### Transfers

Moving a patient completes the current arrangement and opens a new one on the
target bed, linked by `transferred_from_id`. The move is therefore two rows that
form a traceable chain rather than an edit that erases where the patient was.

## Deletion policy

Nothing is hard deleted except an unused room type.

- Rooms are retired by setting `status` to `Out of Service`; beds to
  `maintenance`. Historical arrangements stay valid.
- Arrangements are cancelled by setting `status = 'Cancelled'`, preserving a
  complete audit trail of every bed stay and theatre session.
- Shortage cases are cancelled with a recorded reason.
- A room type can be deleted only when no room references it; the API returns
  400 with the count of rooms still using it.

## Cross-service note

`patient_id` and `admission_id` refer to the Patient & Admission service
(Student 1). They are deliberately **not** foreign keys — that data lives in a
separate database microservice, so the constraint cannot be declared here.
Seeded values (1001–1025 / 5001–5025) are placeholders and must be reconciled
with Student 1's records before integration.

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET POST | `/db/<resource>` | List (with filters) and create |
| GET PUT DELETE | `/db/<resource>/<id>` | Read, update, delete |
| GET | `/db/views/availability` | Beds joined to room and type |
| GET | `/db/views/theatre-board` | Theatres with their current or next session |
| GET | `/db/views/occupancy-stats` | Counts by care category, ward and theatre state |
| GET | `/db/arrangements/overlaps` | Active arrangements clashing with a proposed window |
| GET | `/health` | Service state and record counts |

`<resource>` is one of `room-types`, `rooms`, `beds`, `arrangements`,
`shortage-cases`.

This service holds **no workflow rules**. Conflict detection, status transitions
and AI calls all live in the backend, so the data layer stays a thin, testable
boundary.

## Consistency guarantees

`init_db.py --check` verifies the invariants that the API layer must also
maintain, and the same checks run in CI and in `tests/test_seed_data.py`:

- every table has at least 10 records
- no foreign key violations
- a bed marked `occupied` has exactly one `In Progress` arrangement, and vice versa
- no two active arrangements overlap in time on the same bed
- a room marked `In Use` has an occupied bed; one marked `Available` has none
- a resolved shortage case records who decided and when
- a transfer points at an arrangement for the same patient
