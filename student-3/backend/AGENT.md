# Scheduled pharmacy agent (criterion 4)

The backend's `python app.py` entrypoint starts one daemon thread. The agent
uses the existing reorder candidate calculations, AI client and per-item
fallback. It can only insert purchase orders awaiting human approval.

## Configuration and demo

Only the Student 3 backend compose block adds these settings:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_ENABLED` | `true` | Start the scheduled thread; set `false` to disable it. |
| `AGENT_INTERVAL_SECONDS` | `300` | Seconds between cycle starts; use `60` for a demo. |
| `AGENT_MAX_PROPOSALS` | `3` | Maximum proposals saved per cycle. |
| `AGENT_BUDGET_CAP` | `500.00` | Maximum combined value of proposals saved per cycle. |

Run from the repository root:

```sh
AGENT_INTERVAL_SECONDS=60 docker compose up -d --build student-3-backend student-3-frontend
docker compose logs -f student-3-backend
```

The interval measures cycle starts. If a cycle takes longer than the interval,
the worker waits another interval after it finishes. Cycles never overlap.
Failures are logged and the worker continues. It does not automatically retry
a failed order POST because a lost response may follow a successful insert;
the next cycle checks persisted orders again.

Open `http://localhost:3300/purchase-orders` as a **Pharmacy Manager**. The
Agent panel refreshes every five seconds and links to the approval queue.
Refresh the queue to see new proposals. Expand an order row to see its reasoning,
reject it with a reason, or replace it with an edited quantity. AI badges mark
agent/AI proposals; reasoning explicitly identifies rule-based fallback when used.

## What the terminal shows

Every agent line includes a UTC timestamp, cycle number and named stage.

1. **PLAN** uses the existing reorder service to gather available stock,
   30-day issue usage, supplier lead times, open orders and near-expiry stock.
   It reports candidate counts and applies recent human feedback.
2. **ACT** calls the existing reorder advisory with the versioned v2 prompt.
   v1 remains in use for the existing manual suggestion endpoint. The optional
   v2 context contains up to ten recent rejected/edited AI orders and their
   reasons. The model only supplies reasoning and priority; backend code owns
   quantities. The original per-item fallback is unchanged.
3. **OBSERVE** prints each draft's reasoning and every check as PASS or FAIL:
   open/pending order, cumulative cycle budget, expiry risk, normal usage, and
   whether human feedback changed while Act was running. Failed drafts are
   dropped with reasons. Eligible drafts beyond the proposal limit are deferred.
4. **ADAPT** reloads the latest ten rejected/edited AI orders, logs their reasons
   and reports created/rejected counts. Feedback is refreshed again before the
   next Act to include decisions made between cycles.

Survivors are inserted with `status=pending_approval`, `ai_generated=1`,
`created_by=agent`, and populated `ai_reasoning`. The agent never approves,
orders, receives, issues or dispenses stock.

## Human feedback and quantity changes

A rejection normally suppresses that medicine while its decision remains in
the ten most recent feedback records. A reason containing **“maximum 40 units”**
or **“at most 40 units”** permits a smaller replacement, but only if that limit
is positive and below the rejected quantity. Quantities are capped by both the
existing reorder calculation and the explicit human limit. Repeating the same
or a larger rejected quantity is blocked. These rules also apply without AI.

For example, reject an 80-unit proposal with:

> Quantity is too high for the demand review; maximum 40 units to reduce waste.

The next cycle can propose 40 units with the rejection reference in its reasoning,
provided all Observe checks pass. Generic reasons are never interpreted as
permission to guess a new quantity.

The database API cannot update an order's quantity. Editing an AI proposal
therefore retains the original as cancelled, stores an `EDITED` decision with
the revised quantity, and creates a replacement still awaiting approval. This
uses existing database fields and endpoints, without changing the database
service. If replacement creation fails, the original remains cancelled for
human review; no potentially duplicate POST is retried automatically.

## Operating assumptions

- Incoming orders have no expiry date or guaranteed shelf-life field. Observe
  explicitly logs a **90-day planning assumption**, including stock projected
  to remain at arrival; this is not a claim about actual batch expiry. Zero
  recent usage fails the check. A separate quantity check caps drafts at
  60 days of observed usage. These conservative rules can defer legitimate
  low-frequency purchases for human review.
- The supplied Docker entrypoint runs **one backend process**. A shared lock
  serialises the agent's duplicate-check/create operation with human order
  writes through that backend. Multiple processes/replicas or writers calling
  the database API directly would require database-side locking/uniqueness
  before enabling the agent. Importing `app` for tests does not start a worker.
- Recent feedback is stored in existing purchase orders and survives backend
  restarts; dashboard cycle counters reset when the backend restarts.
- The seeded orders predate this feature and lack decision timestamps, so their
  feedback falls back to creation time and order ID. New rejections/edits record
  an explicit UTC decision time in `decision_reason`.

## Verification

Run the automated tests without starting Ollama:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s student-3/backend/tests -v
```

The live demonstration uses a fresh disposable database, host ports 15300/13300,
and a separate Ollama container reading the already-downloaded model volume.
The existing application database and shared Ollama are not stopped or edited.
Recorded terminal output, verification events, rendered frontend HTML and order
records are in `evidence/`. See `evidence/README.md` for the measured results.
