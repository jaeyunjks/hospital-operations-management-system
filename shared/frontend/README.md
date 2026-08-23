# Shared Frontend — Design System

The shared UI foundation for the **Hospital Operations Management System**.
Every student module builds on these assets so the integrated application reads
as one product rather than five.

Extracted from the approved Claude Design mockup
(*HOMS Staff and Shift Management*, offline v2).

> **Scope:** design system only. No feature pages are implemented here.

## Contents

```
shared/frontend/
├── index.html              # placeholder landing shell
├── css/
│   ├── main.css            # entry point — imports the four files below
│   ├── variables.css       # design tokens (colour, type, spacing, radius, shadow)
│   ├── reset.css           # browser normalisation + base typography
│   ├── layout.css          # app shell, page structure, grid
│   └── components.css      # cards, buttons, badges, tables, AI elements
└── assets/
    ├── icons/              # shared inline SVG icons
    └── images/             # shared images and illustrations
```

## Design language

### Colour

| Role | Token | Value |
|------|-------|-------|
| Canvas | `--color-bg-canvas` | `#F8FAFC` |
| Surface | `--color-bg-surface` | `#FFFFFF` |
| Sunken surface | `--color-bg-surface-sunken` | `#F5F7FA` |
| Primary text | `--color-text-primary` | `#0F172A` |
| Secondary text | `--color-text-secondary` | `#475569` |
| Subtle border | `--color-border-subtle` | `#EEF2F7` |
| **Primary action** | `--color-action-primary` | `#2563EB` |
| **AI accent** | `--color-ai-accent` | `#6366F1` |
| AI wash / border / ink | `--color-ai-*` | `#F5F3FF` / `#DDD6FE` / `#4338CA` |

Status colours: success emerald, warning amber, danger red, info blue, neutral
slate, review violet — each with a matching `-fg` and `-bg` token.

**Blue means action. Indigo/violet means AI.** These two never swap roles.

### Typography

`Inter` for UI, `IBM Plex Mono` for identifiers and codes. Scale runs from
`--fs-micro` (11px) through `--fs-display-xl` (52px); body default is 14px.
Numeric columns use `font-variant-numeric: tabular-nums` so figures align.

### Spacing, radius, elevation

- **Spacing:** 4px base scale — `--space-1` (4px) … `--space-24` (96px).
- **Radius:** `--radius-sm` 4px, `md` 7px (controls), `lg` 10px, `xl` 12px (cards), `full` pill.
- **Shadows:** `--shadow-xs` on resting cards, `sm`/`md` on hover and popovers,
  `lg` for modals. Elevation is subtle — borders carry most of the separation.

## Reusable classes

| Class | Purpose |
|-------|---------|
| `.card` | White surface container. `--flush` for edge-to-edge tables, `--interactive` for clickable cards |
| `.stat-card` | Dashboard metric: `__label`, `__value`, `__meta` |
| `.btn-primary` | Main action — blue |
| `.btn-secondary` | Secondary action — white with border |
| `.btn-ghost` / `.btn-danger` / `.btn-ai` | Low-emphasis / destructive / AI-triggering |
| `.badge-success` | Positive state (rostered, administered, discharged) |
| `.badge-warning` | Attention needed (understaffed, due soon) |
| `.badge-danger` | Critical (unstaffed, overdue, missed dose) |
| `.badge-info` / `.badge-neutral` / `.badge-review` | Informational / default / awaiting review |
| `.table-container` + `.table` | Scrollable data table with sticky-capable header |
| `.ai-card` | AI recommendation panel |
| `.ai-badge`, `.ai-suggestion`, `.ai-score`, `.ai-disclaimer` | AI sub-components |
| `.field`, `.input`, `.select`, `.textarea` | Form controls |
| `.notice`, `.empty-state`, `.avatar`, `.divider` | Supporting pieces |

Layout: `.app-shell`, `.app-sidebar`, `.app-topbar`, `.page`, `.page-header`,
`.grid--2/3/4/auto/sidebar`, `.stack`, `.row`.

## How student features import these styles

Each Flask module serves the shared directory as static files, then links
`main.css` — the single import that pulls in all four stylesheets in order.

**1. Expose the shared folder from your Flask app:**

```python
app = Flask(__name__, static_folder="static")
```

Mount `shared/frontend` at `/static/shared` (a symlink, a Docker volume, or a
`COPY` step in your Dockerfile — whichever suits your service).

**2. Link it from your template:**

```html
<link rel="stylesheet" href="/static/shared/css/main.css">
```

**3. Use the shared classes — do not restyle them:**

```html
<div class="card">
  <div class="card__header">
    <h2 class="card__title">Today's Roster</h2>
    <div class="card__actions">
      <button class="btn-secondary btn-sm">Export</button>
      <button class="btn-primary btn-sm">Add Shift</button>
    </div>
  </div>
  <div class="table-container">
    <table class="table">
      <thead>
        <tr><th>Staff</th><th>Role</th><th>Status</th></tr>
      </thead>
      <tbody>
        <tr>
          <td>Amara Okafor</td>
          <td class="table__cell--muted">Registered Nurse</td>
          <td><span class="badge-success">Confirmed</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
```

**AI recommendation panel:**

```html
<section class="ai-card">
  <div class="ai-card__header">
    <span class="ai-badge">AI Suggested</span>
  </div>
  <button class="ai-suggestion">
    <div class="ai-suggestion__main">
      <div class="ai-suggestion__title">Chloe Bennett</div>
      <div class="ai-suggestion__reason">Available · Role matches · No clash</div>
    </div>
    <span class="ai-score">92%</span>
  </button>
  <p class="ai-disclaimer">Recommendations require human review before rostering.</p>
</section>
```

### Module-specific styles

Keep them in your own module and build on the tokens — never hard-code a colour:

```css
/* student-N/frontend/css/feature.css */
.roster-slot--gap { border-left: 3px solid var(--color-status-danger-fg); }
```

Load order is always shared first, module second:

```html
<link rel="stylesheet" href="/static/shared/css/main.css">
<link rel="stylesheet" href="/static/css/feature.css">
```

## Rules

1. **Consume tokens, not raw values.** No hex codes in feature CSS.
2. **One primary action per view.** Everything else is secondary or ghost.
3. **Primary buttons are blue, never black.**
4. **Indigo/violet is reserved for AI.** Never use it for ordinary actions.
5. **Label AI regions once.** One `.ai-badge` per panel — not per row.
6. **Icons carry no background.** No coloured tiles or circles behind them.
7. **Never rely on colour alone** for status — always include a text label.
8. **Do not edit shared files for one module's needs.** Raise it with the team.

## Accessibility

All text/background pairs in the token set meet **WCAG 2.1 AA (4.5:1)**. Three
values were darkened from the mockup to reach that bar:

| Token | Mockup | Adjusted | Reason |
|-------|--------|----------|--------|
| `--color-action-primary` | `#3B82F6` | `#2563EB` | White on blue-500 was 3.68:1 |
| `--color-text-tertiary` | `#94A3B8` | `#64748B` | slate-400 on white was 2.56:1 |
| `--color-status-warning-fg` | `#B45309` | `#92400E` | Was 4.45:1 on the amber wash |

Focus states are never removed (`:focus-visible` outline plus
`--color-focus-ring`), and `prefers-reduced-motion` is respected.

## Browser support

Modern evergreen browsers. Uses CSS custom properties, grid, and flexbox — all
baseline-supported and requiring no build step, consistent with the ASD stack
(HTML5 / CSS3 / HTMX, no frontend framework).
