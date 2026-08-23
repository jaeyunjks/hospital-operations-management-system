# Shared Icons

Inline SVG icons shared across hospital modules.

## Conventions

- **Stroke, not fill.** Use `stroke="currentColor"` and `fill="none"` so an
  icon inherits the colour of its container.
- **24×24 viewBox**, `stroke-width="2"`, round caps and joins.
- **Size with CSS**, not hard-coded width/height — 16px inside buttons,
  20px standalone.
- **No background behind icons.** No coloured circles, tiles, or wells; the
  icon sits directly on the surface.
- **Decorative icons** get `aria-hidden="true"`. An icon that is the only
  content of a control needs an accessible name on the control itself.

## Example

```html
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M12 5v14M5 12h14" />
</svg>
```

No icons are committed yet. Add shared ones here; keep module-specific icons in
that module's own frontend directory.
