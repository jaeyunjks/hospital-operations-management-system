# student-4 — Room & Bed Management

Owner: **Jisoo Jung (Asher)** · Student 4 · HOMS Group 10

Manages hospital rooms, beds and operating theatres, tracks their real-time
availability, handles bed shortages and transfers, and uses a local LLM to
suggest suitable rooms for a coordinator to approve.

Primary user: **Bed & Room Coordinator** (persona 3). The Hospital Operations
Manager and the Admissions Coordinator hold read/limited access to the same data.

## Three microservices

Ports follow the HOMS architecture diagram — student-4 is the 3400 / 5400 / 6400 block.

| Service | Port | Role |
|---------|------|------|
| `frontend/` | 3400 | HTMX UI. Calls the backend only; holds no business rules. |
| `backend/`  | 5400 | Workflow rules, conflict detection, AI integration. |
| `database/` | 6400 | The only process that opens `rooms.db`. Plain CRUD plus three joined views. |

Each has its own `Dockerfile`, as agreed by the team: one container, one process.

```
frontend (3400) ──HTTP──> backend (5400) ──HTTP──> database (6400) ──> rooms.db
                                │
                                └──HTTP──> Ollama (11434) ──> approved LLM
```

The backend never opens the SQLite file, and no other team service reads it —
cross-service access is by API only, per the HOMS collaboration rules.

## Running it locally

```bash
# 1. database
cd student-4/database && python init_db.py --check && python app.py

# 2. backend (new terminal)
cd student-4/backend && pip install -r requirements.txt && python app.py

# 3. frontend (new terminal)
cd student-4/frontend && pip install -r requirements.txt && python app.py
```

Then open <http://localhost:3400>.

With Docker Compose from the repository root: `docker compose up student-4-database
student-4-backend student-4-frontend`.

## Tests

```bash
python -m pytest student-4/tests -v
```

62 tests covering seed-data integrity, CRUD, conflict detection, transfers, the
shortage workflow and AI failure handling. No network and no ports are needed:
the backend's database client is redirected into the database service's own test
client, and each test builds a fresh database in a temporary directory.

## Owned data

| Table | Holds |
|-------|-------|
| `room_types` | The 10 room types, each mapped to a care category |
| `rooms` | Physical rooms, including the three operating theatres |
| `beds` | Allocatable slots; an operating table is a bed |
| `room_arrangements` | Inpatient stays **and** theatre sessions, including transfers |
| `shortage_cases` | Bed shortage workflow (Architecture v2.2 §5.2) |

### Care categories

Every room type is `Surgical`, `Short-term` or `Long-term`. Placement narrows
candidates to the matching category before anything else, which keeps patients
with similar care needs together and gives the AI a small, reliable set to rank.

### Why stays and surgery share one table

A theatre session and an inpatient stay are both "a patient occupying a slot
between two times". One table means the double-booking check is written once and
covers both. `purpose` distinguishes them.

### Deletion policy

Nothing is hard deleted except an unused room type.

- Rooms → `Out of Service`; beds → `maintenance`
- Arrangements → `Cancelled`, never removed
- Shortage cases → `Cancelled` with a recorded reason
- A room type can be deleted only when no room references it

## AI integration

Required Release 0 flow: **frontend → backend/API → Ollama → approved LLM → backend → frontend**.

| Endpoint | Does |
|----------|------|
| `POST /api/rooms/suggest` | Classifies requirements into a care category, filters available beds, asks the model to rank them with reasons |
| `POST /api/rooms/occupancy-summary` | Plain-language summary of ward occupancy and theatre utilisation |

Both are **advisory**. Architecture v2.2 §5.2: *"AI never assigns a bed; an
authorised employee approves the allocation."* Neither endpoint writes room, bed
or arrangement state — a test asserts this.

Three safeguards, because a small local model is unreliable:

1. **Timeout on every call** (`OLLAMA_TIMEOUT`, default 30s), so a stalled model
   cannot freeze the coordinator's screen.
2. **Output validated against the real candidate list.** A bed the model invented
   is discarded and reported in `discarded_ids`.
3. **Rule-based fallback.** If the model is absent, slow or returns unusable
   output, deterministic ordering is shown and labelled as such. The feature
   never goes blank.

### Plan → Act → Observe → Adapt

| Stage | In this feature |
|-------|-----------------|
| Plan | Classify the request into a care category; build the candidate set of free beds in that category |
| Act | Send the candidates to Ollama, which ranks them and gives a reason for each |
| Observe | Validate the reply against the real candidates; record whether the coordinator accepted or overrode it, and any booking refused by conflict detection |
| Adapt | Every later suggestion is recomputed against current occupancy, so the next answer reflects the ward as it is now |

## Configuration

Every value is an environment variable, so the same image runs locally, in
Compose and in the cloud.

| Variable | Default | Service |
|----------|---------|---------|
| `PORT` | 3400 / 5400 / 6400 | all |
| `DB_PATH` | `database/rooms.db` | database |
| `DATABASE_URL` | `http://localhost:6400` | backend |
| `BACKEND_URL` | `http://localhost:5400` | frontend |
| `OLLAMA_URL` | `http://localhost:11434` | backend |
| `OLLAMA_MODEL` | `qwen2.5:3b` | backend |
| `OLLAMA_TIMEOUT` | `30` | backend |
| `AUTH_URL` | `http://localhost:5000` | backend |
| `AUTH_ENABLED` | `0` | backend |

`AUTH_ENABLED=0` returns a development identity so this feature can be built and
demonstrated before the shared Authentication service (3000/5000) is ready.
Switching it to `1` validates every request against that service; no route code
changes.

## API

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET POST PUT DELETE | `/api/room-types` | Room type CRUD (delete blocked while in use) |
| GET POST PUT DELETE | `/api/rooms` | Room CRUD (delete is soft) |
| PUT | `/api/rooms/{id}/status` | Set In Use / Cleaning / Out of Service |
| GET POST PUT DELETE | `/api/beds` | Bed CRUD (delete is soft) |
| GET | `/api/rooms/availability` | Real-time availability by care category, status, ward |
| GET | `/api/theatres/board` | Theatre states with current or next session |
| GET | `/api/wards/occupancy` | Per-ward bed counts, published for other services |
| GET POST PUT | `/api/arrangements` | Bed stays and theatre sessions |
| PUT | `/api/arrangements/{id}/release` | Discharge or end a session |
| PUT | `/api/arrangements/{id}/cancel` | Soft delete |
| POST | `/api/arrangements/{id}/transfer` | Move a patient to another bed |
| GET POST | `/api/shortage-cases` | Open and list shortage cases |
| GET | `/api/shortage-cases/{id}/options` | Compatible options in priority order |
| PUT | `/api/shortage-cases/{id}/decide` | Record the choice and its mandatory reason |
| PUT | `/api/shortage-cases/{id}/resolve` `/cancel` | Close a case |
| POST | `/api/rooms/suggest` | **AI** — ranked bed recommendations |
| POST | `/api/rooms/occupancy-summary` | **AI** — occupancy summary |
| GET | `/health` | Own state plus database and AI reachability |

All responses use the team envelope:

```json
{"success": true, "data": null, "error": null}
```

## Published contract: ward occupancy

`GET /api/wards/occupancy` (optional `?ward=`) is a read-only endpoint other
services depend on. Staff & Shift Management consumes it for staffing context
and later AI shift planning, so the field names below are a contract, not an
internal detail. `tests/test_ward_occupancy.py` pins the names and the
arithmetic so a breaking change fails a test first.

```json
{
  "wards": [
    {
      "ward": "Critical Care",
      "total_beds": 4,
      "occupied": 2,
      "available": 1,
      "reserved": 1,
      "maintenance": 0,
      "monitored_beds": 4,
      "occupancy_pct": 50.0,
      "care_categories": ["Short-term"]
    }
  ],
  "totals": { "total_beds": 25, "occupied": 9, "occupancy_pct": 36.0 }
}
```

`occupied + available + reserved + maintenance == total_beds` always holds.
No AI is involved, so the numbers are available whether or not Ollama is
running.

## Cross-service notes

- `room_arrangements.patient_id` and `admission_id` belong to the Patient &
  Admission service (Student 1). They are **not** foreign keys — that data lives
  in a separate database. Seeded values are placeholders and must be reconciled
  with Student 1's records before integration.
- Architecture v2.2 Table 14 defines the current allocation as **Admission ↔ Bed**,
  so `admission_id` is the precise link. It is nullable until Student 1's
  admission identifiers are agreed.
- `surgeon_name` is stored as text, not a `staff_id` from Staff & Shift
  Management. If the roster needs to know a surgeon is in theatre, add
  `surgeon_id` alongside it and keep the name as a display snapshot.
