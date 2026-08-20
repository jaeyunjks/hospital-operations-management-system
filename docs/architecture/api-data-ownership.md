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
