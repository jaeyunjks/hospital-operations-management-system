# HOMS Shared Design System

The shared frontend foundation for the **Hospital Operations Management System**.
All five feature microservices consume this system so the integrated application
reads as one product.

Derived from the approved HOMS design (*Staff & Shift Management*, v3), which is
the visual source of truth.

## Design intent

Precise, restrained, enterprise-grade, information-dense. Built for desk
monitors and long shifts.

Structure is carried by **hairline borders, alignment and typography** — not by
radius, shadow, or colour. The interface is **square by design** (`--radius-*: 0`)
and effectively **shadow-free** (elevation is reserved for dialogs). There are no
gradients, no glassmorphism, and no decorative animation.

## Contents

```
shared/frontend/
├── index.html          # CoreBoard — shared entry point
├── css/
│   ├── main.css        # entry point — imports the four below, in order
│   ├── variables.css   # design tokens
│   ├── reset.css       # normalisation + base elements
│   ├── layout.css      # application shell, grids, structure
│   └── components.css  # reusable components
└── assets/
    ├── icons/          # shared inline SVG
    └── images/
```

## Typography

| Face | Token | Used for |
|------|-------|----------|
| Source Serif 4 | `--font-display` | **Major titles only** — `h1`–`h3`, page and brand |
| Barlow | `--font-ui` | Everything operational: nav, tables, forms, buttons, KPI labels |
| IBM Plex Mono | `--font-mono` | Identifiers, numerals, uppercase micro-labels |

Serif never appears in dense operational content. Base UI size is **13.5px** —
dense but readable. Numeric columns use `tabular-nums` so figures stay aligned.

Fonts are linked from Google Fonts in the shipped pages. **Self-host into
`assets/fonts/` for offline and Docker deployments** — the stacks fall back to
Georgia and the system sans/mono faces, so the UI stays usable either way.

## Colour tokens

Authored in OKLCH for perceptually even ramps. Feature code uses the semantic
layer only — never the `--grey-*` / `--indigo-*` primitives.

| Token | Role |
|-------|------|
| `--color-background` | Application canvas |
| `--color-surface` | Cards, panels, tables |
| `--color-surface-muted` | Sunken wells |
| `--color-surface-band` | Section and table header bands |
| `--color-text-primary` / `-secondary` / `-muted` / `-label` | Text hierarchy |
| `--color-border` / `--color-border-strong` / `--color-border-control` | Hairline / panel / control outlines |
| `--color-accent` / `--color-accent-soft` | The single quiet indigo action colour |
| `--color-success` / `--color-success-soft` | Operational status |
| `--color-warning` / `--color-warning-soft` | Operational status |
| `--color-danger` / `--color-danger-soft` | Operational status |
| `--color-ai` / `--color-ai-soft` | AI / advisory surfaces |

Each status also has a `--color-*-mark` for dots, meters and rules.

**One quiet indigo carries action and focus.** Green, amber and red communicate
operational status only — never decoration.

## Other tokens

- **Spacing** `--space-1` (3px) … `--space-10` (48px), `--gutter` (24px)
- **Radius** `--radius-sm/md/lg` (all `0`), `--radius-dot` (50%), `--radius-pill`
- **Type** `--size-display-1..4`, `--size-ui*`, `--size-data*`, `--size-label*`
- **Controls** `--control-height` (30px), `--row-height` (40px), `--row-pad-*`
- **Layout** `--sidenav-width` (232px), `--header-height` (52px), `--content-max`, `--prose-max`
- **Borders** `--border-hair`, `--border-strong`, `--focus-ring`

## Layout

```
.app > .app-header
       .app-body > .app-sidenav
                   .app-main > .page
```

| Class | Purpose |
|-------|---------|
| `.app-header` | Dark global header: brand, primary nav, role, user |
| `.app-sidenav` | Feature navigation rail |
| `.page`, `.page-header` | Content container and page title block |
| `.toolbar` | Filters/actions above a data region |
| `.stat-row` | KPI row with hairline grid |
| `.layout-split` | Operational content + advisory rail |
| `.layout-detail` | Primary/secondary reading split |
| `.grid--2/3/4`, `.stack`, `.row` | General structure |

None of these are Staff & Shift specific.

## Components

**Navigation** — `.nav-link`, `.sidenav-link`, `.user-chip`, `.avatar`, `.role-select`

**Buttons** — `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.btn-danger`,
`.btn-compact`, `.btn-lg`, `.btn-block`

**Status** — `.status` + `.status__dot` (`--success/--warning/--danger/--info/--pending`),
and `.badge-success` / `-warning` / `-danger` / `-info` / `-neutral` / `-pending`

**Operational** — `.panel` (+ `__header/__body/__footer`), `.section-header`,
`.stat` (+ `__label/__value/__meta`), `.table-wrap` + `.table`, `.meter`,
`.alert` (`--success/--warning/--danger/--info`), `.empty`, `.filter`,
`.input` / `.select` / `.textarea`, `.field`, `.segmented`

**AI / advisory** — `.ai-panel`, `.ai-label`, `.recommendation`, `.ai-basis`,
`.confidence`, `.approval`, `.ai-disclaimer`

`.card` and `.stat-card` are retained as aliases of `.panel` / `.stat` for
continuity with earlier work; new code should prefer `.panel` and `.stat`.

### The AI contract

**AI proposes; a human decides.** Every AI surface must:

1. carry an `.ai-label` so its origin is unambiguous,
2. state its basis with `.ai-basis` rather than asserting authority,
3. end in an explicit human decision (`.ai-decision` / `.approval`).

AI shares the indigo family — it is marked by tint and label, never by
decorative styling, and never visually dominates operational content.

## How to apply this to your feature

**1. Serve the shared directory as static files** from your Flask app — a
symlink, a Docker volume, or a `COPY` step. Then link the single entry point:

```html
<link rel="stylesheet" href="/static/shared/css/main.css">
<link rel="stylesheet" href="/static/css/feature.css">
```

Shared always loads first; your feature CSS second.

**2. Use the shell** so every module looks the same:

```html
<div class="app">
  <header class="app-header">…</header>
  <div class="app-body">
    <nav class="app-sidenav" aria-label="Sections">…</nav>
    <main class="app-main" id="main">
      <div class="page">
        <div class="page-header">
          <div>
            <span class="page-header__eyebrow">Your Module</span>
            <h1 class="page-header__title">Screen name</h1>
          </div>
          <div class="page-header__actions">
            <button class="btn-primary">Primary action</button>
          </div>
        </div>
        <!-- content -->
      </div>
    </main>
  </div>
</div>
```

**3. Compose from shared components** — a KPI row and a table:

```html
<div class="stat-row">
  <div class="stat">
    <span class="stat__label">Occupied beds</span>
    <span class="stat__value" data-numeric>128</span>
    <span class="stat__meta">
      <span class="status status--warning"><span class="status__dot"></span>Near capacity</span>
    </span>
  </div>
</div>

<div class="table-wrap">
  <table class="table table--zebra">
    <thead><tr><th scope="col">Ward</th><th scope="col">Status</th></tr></thead>
    <tbody>
      <tr>
        <td class="table__name">Ward 4B</td>
        <td><span class="badge-danger">At capacity</span></td>
      </tr>
    </tbody>
  </table>
</div>
```

**4. Keep feature CSS minimal.** Only styles that no other module could reuse
belong locally, and they must build on tokens:

```css
/* student-N/frontend/css/feature.css */
.bed-cell { display: flex; gap: var(--space-3); }
```

See `student-5/frontend/` for a worked reference implementation.

## Rules

1. **Consume tokens, never hex codes.** No raw colour in feature CSS.
2. **One primary action per view.** Everything else is secondary or ghost.
3. **Indigo is the only accent.** Green/amber/red mean operational status.
4. **Never colour alone** — always pair a dot or badge with text.
5. **Label AI once per region**, and always end in a human decision.
6. **Do not edit shared files for one module.** Raise it with the team.
7. **No new radii, shadows, or gradients.** The system is flat and square.

## Accessibility

Every text pairing in the semantic layer meets **WCAG 2.1 AA (4.5:1)**. Values
were darkened from the raw design where needed:

| Token | Ratio | Note |
|-------|-------|------|
| `--color-text-secondary` on surface | 12.9:1 | Body |
| `--color-text-muted` on surface | 7.0:1 | Supporting |
| `--color-text-label` on surface | 5.9:1 | Darkened; the design's label grey was 4.2:1 |
| `--color-success` on `-soft` | 6.1:1 | Darkened from 4.2:1 |
| `--color-warning` on `-soft` | 6.2:1 | Darkened from 2.8:1 |
| `--color-danger` on `-soft` | 6.3:1 | |
| `--color-accent` on surface | 6.0:1 | Links |
| white on `--color-accent` | 6.1:1 | Primary button |
| `--color-border-control` on surface | 3.2:1 | Meets 3:1 for control boundaries |

Also: visible `:focus-visible` on everything, a `.skip-link` first tab stop,
`prefers-reduced-motion` respected, semantic buttons vs links, `scope` on table
headers, and 30px minimum control height.

## Browser support

Modern evergreen browsers. Uses CSS custom properties, grid, flexbox and
**OKLCH colour** (baseline since 2023). No build step and no frontend
framework — consistent with the ASD stack.
