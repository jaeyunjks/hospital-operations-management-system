# Pharmacy AI Advisories and Scheduled Agent

## Advisory-only AI

The pharmacy feature uses a local Ollama model only for advice. The principle
is "the backend owns the numbers; the model owns the judgement": medicine IDs,
quantities, dates, usage rates, values and suggested reorder quantities always
come from inventory data and are never accepted from the model. The model only
supplies judgement fields such as priority, recommended action and reasoning.

Model replies must be valid JSON matching the prompt's schema. A malformed
reply is retried once; if it still fails, or the model times out or is
unavailable, the feature falls back to a deterministic rule-based result marked
with source `fallback`. AI replies are marked with source `ai`.

## Reorder suggestions

The AI reorder suggestion reviews backend-calculated reorder candidates and
adds a priority, reasoning and an optional adjustment flag. A manager can turn
chosen suggestions into draft purchase orders, which are created with status
`pending_approval` and marked as AI-generated.

## Scheduled agent

The scheduled pharmacy agent runs a Plan, Act, Observe, Adapt cycle in the
background, every 300 seconds by default:

- **Plan** — reads stock, 30-day usage, supplier lead times, open orders and
  near-expiry stock, and finds reorder candidates.
- **Act** — drafts reasoned reorder proposals using the model or the rule
  fallback.
- **Observe** — rejects duplicate proposals, proposals over the budget cap,
  quantities likely to expire, and quantities far above normal usage.
- **Adapt** — reads the ten most recent rejected or edited AI orders and their
  decision reasons, and uses them in the next reorder prompt.

By default the agent creates at most 3 proposals per cycle with a total value
of at most $500.00. It only ever creates `pending_approval` purchase orders
marked as created by `agent`. It never approves, orders, issues or dispenses
stock, so every proposal still needs a Pharmacy Manager's approval.
