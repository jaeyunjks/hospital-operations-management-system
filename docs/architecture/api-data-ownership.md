# API & Data Ownership

_Placeholder — to be defined by the team._

To keep the five microservices independent, each student will own their own API
surface and their own SQLite database. Cross-feature access should happen via
published APIs, not by reaching into another service's database.

The mapping of feature area to `student-N` directory has **not** been decided;
the table below uses neutral placeholders. See
[`feature-ownership.md`](feature-ownership.md).

| Directory   | Feature area | API base path | Owns data for |
|-------------|--------------|---------------|---------------|
| `student-1` | _TBD_        | _TBD_         | _TBD_         |
| `student-2` | _TBD_        | _TBD_         | _TBD_         |
| `student-3` | _TBD_        | _TBD_         | _TBD_         |
| `student-4` | _TBD_        | _TBD_         | _TBD_         |
| `student-5` | _TBD_        | _TBD_         | _TBD_         |

Endpoint contracts, request/response schemas, and shared data conventions are
**to be agreed** and documented here as they are designed. Do not invent
endpoints or schemas before they are agreed.

## Service port allocation

Ports follow the approved microservice architecture diagram. Each service set
uses a consistent offset: `3N00` UI, `5N00` API, `6N00` database.

| Directory   | Feature area | UI | API | Database |
|-------------|--------------|------|------|------|
| `student-1` | _TBD_        | 3100 | 5100 | 6100 |
| `student-2` | _TBD_        | 3200 | 5200 | 6200 |
| `student-3` | _TBD_        | 3300 | 5300 | 6300 |
| `student-4` | _TBD_        | 3400 | 5400 | 6400 |
| `student-5` | _TBD_        | 3500 | 5500 | 6500 |

Shared services:

| Service | UI | API |
|---------|------|------|
| Shared Authentication | 3000 | 5000 |

Every service must make its port configurable by environment variable so
Docker Compose can override it. Student 5 uses `BACKEND_PORT`,
`DATABASE_SERVICE_PORT`, and `DATABASE_SERVICE_URL`.
