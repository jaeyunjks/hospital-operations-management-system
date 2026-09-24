# Batch Expiry and Write-Off

## Expiry categories

Every medicine batch has an expiry date and a remaining quantity. The number of
days until expiry decides its category:

- **Expired** — the expiry date has passed (fewer than 0 days remaining).
- **Expiring within 7 days** — 0 to 7 days remaining; shown as urgent on the
  dashboard.
- **Expiring soon** — 0 to 30 days remaining.
- **Valid** — more than 30 days remaining.

The batches page can also filter batches expiring within 90 days. Empty
batches (no remaining quantity) are hidden unless requested.

## Writing off a batch

Writing off removes a batch's remaining stock, usually because it has expired
or been damaged. Only a Pharmacy Manager can write off a batch, and a reason is
required (the default reason is "Expired"). A batch that is already empty
cannot be written off.

A write-off sets the batch's remaining quantity to zero, recalculates the
medicine's stock quantity from its non-expired batches, and appends a `waste`
movement to the stock ledger. A write-off cannot be undone.

## Expiry and waste advisory

The AI expiry advisory reviews batches expiring within a chosen window
(30 days by default) and estimates potential waste using stock issued in the
last 30 days. For each batch it gives a recommended action, a priority (high,
medium or low) and short reasoning. The advisory is read-only: it never writes
off, issues or changes stock. If the AI model is unavailable, a rule-based
advisory is shown instead and clearly labelled as not AI-reviewed.
