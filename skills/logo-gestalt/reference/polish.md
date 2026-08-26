# polish — mark system and delivery

Turn distilled finalist(s) into a **shippable mark system** — not just a single SVG file.

## Inputs

- `.logo-gestalt/marks/final-*.svg`
- `BRAND.md` + `BRIEF.md`
- Latest previews

## Deliverables checklist

### Core files

- [ ] `final.svg` — master construction (512 viewBox)
- [ ] `final-mono-black.svg` — `#000` on transparent
- [ ] `final-mono-white.svg` — `#fff` on transparent (for dark UI)
- [ ] PNG exports: 1024, 512, 64, 32 (via `build_previews.py`)

### App icon

- [ ] Square crop with safe padding (~10% margin)
- [ ] No hinge annotation in production assets
- [ ] iOS/Android corner radius mock optional in board

### Clear space

Document minimum padding: typically **½×** mark height or width of key feature (state rule in prose).

### Minimum size

Smallest approved use (e.g. 16px favicon) with pass/fail from ladder.

### Color (if brief allows)

- Primary brand color on mark OR on background
- Mono remains source of truth; color is variant layer

### Wordmark lockup (if in brief)

- Horizontal and stacked variants
- Gap between mark and type = x-height or agreed unit

### Construction notes

Update `CONSTRUCTION.md` with final hinge coordinates and export settings.

### Don'ts

- Raster-only delivery without SVG
- Strokes that won't survive favicon
- Gradients required to parse shape

## File hygiene

- Decimal precision ≤2 in paths unless required
- Remove guide layers and comments marked `debug`
- `aria-label` on root SVG

## User-facing summary

One paragraph:

- What the mark reads as
- Hinge (if gestalt)
- Files and where they live
- Recommended primary use (icon vs marketing)

## Handoff

Work complete unless user wants animation or brand guidelines doc (out of scope unless requested).
