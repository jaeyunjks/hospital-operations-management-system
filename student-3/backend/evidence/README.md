# Scheduled agent live verification

This directory records the criterion 4 demonstration against a fresh,
disposable Student 3 database. It used an isolated backend on port 15300, an
isolated frontend on port 13300, and an isolated Ollama container that mounted
the already-downloaded model read-only. The regular Student 3 database and the
shared `homs-ollama` container were not modified or stopped.

The run used `AGENT_INTERVAL_SECONDS=60`, `AGENT_MAX_PROPOSALS=3`, and
`AGENT_BUDGET_CAP=500.00`.

Results:

- Cycle 1: three pending AI proposals were created. The terminal recorded all
  PLAN, ACT, OBSERVE, and ADAPT stages plus PASS/FAIL results for every
  required Observe check.
- Cycle 2: two different proposals were created. The verifier confirmed that
  no medicine received a duplicate pending proposal.
- A reviewer rejected Ondansetron 4mg x80 with an explicit `maximum 40 units`
  reason. Cycle 3's Adapt stage read that rejection and created Ondansetron
  x40, retaining the reason in `ai_reasoning`.
- A reviewer edited another AI proposal. The original was retained as
  cancelled and a pending replacement was created, proving the existing
  database API's restricted update capability is handled safely.
- The isolated Ollama container was stopped before cycle 4. The cycle still
  completed with `source=fallback` and a live agent thread.
- The rendered frontend HTML confirms the approval queue AI badge, Agent panel
  fields, and manager-only reject/edit controls.

`live-verification.txt` is the concise terminal transcript. The full local
terminal log is intentionally ignored by the repository's global `*.log`
rule but was available during the demonstration for screenshots.
