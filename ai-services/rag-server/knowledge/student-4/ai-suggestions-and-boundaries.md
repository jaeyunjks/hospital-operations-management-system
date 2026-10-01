# AI Room Suggestions and Their Boundaries

## What the AI does and does not do

The suggestion endpoint ranks candidate beds for a coordinator to review. The AI
never assigns a bed: an authorised employee approves every allocation. Nothing
the model returns changes bed status, creates an arrangement or resolves a
shortage case.

## How a suggestion is produced

1. **Plan** - the patient's free-text requirements are classified into a care
   category, and the candidate set is built from free beds in that category whose
   room is usable.
2. **Act** - the candidates are sent to the local model, which ranks them and
   gives a reason for each.
3. **Observe** - the reply is validated against the real candidate list, and
   whether the coordinator accepted or overrode it is recorded.
4. **Adapt** - later suggestions are recomputed against current occupancy, so the
   next answer reflects the ward as it is now.

## Classification is deterministic

The care category is chosen by keyword scoring, not by the model, because
classification decides which beds a patient may be placed in and must be
reproducible and explainable. When no keyword matches, the request defaults to
Short-term and the reason says so.

## Fallback when the model is unavailable

If the model cannot be reached or its reply cannot be trusted, a deterministic
ranking is used instead: beds that require monitoring first for categories that
need them, then smaller rooms, then room number for a stable order. The response
makes clear that the ranking was not model-reviewed.
