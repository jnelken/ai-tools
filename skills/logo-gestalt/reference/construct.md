# construct — mono-first SVG candidates

Turn shortlist matches into **3–5** construction-ready SVG marks.

## Outputs

- `.logo-gestalt/marks/candidate-*.svg` (or descriptive names)
- `.logo-gestalt/CONSTRUCTION.md` from `<skill-dir>/templates/CONSTRUCTION.md`

## Inputs

- `.logo-gestalt/MATCHES.md` shortlist
- `.logo-gestalt/BRIEF.md` constraints
- [gestalt.md](gestalt.md) hinge vocabulary

## Rules

### Mono-first

- Single fill color (`#000` on transparent, or `#fff` on `#000` inverse test).
- No gradients, shadows, or multi-color reads in v1.
- Solve silhouette before interior detail.

### Canvas

- `viewBox="0 0 512 512"` unless brief requires wide lockup.
- Center optical mass; leave ~8–12% margin for favicon safe zone.
- Use integer coordinates where possible; snap hinge to half-pixels only when necessary for crisp 1px strokes at small sizes.

### Path discipline

- Prefer **one compound path** or boolean-clean groups over stacked shapes.
- Name layers in comments: `<!-- hinge: vertical spine -->`.
- Avoid hairline strokes for structure — use filled shapes (strokes vanish at 32px).

### Candidate spread

Produce **3–5** distinct directions from the shortlist:

| Type | Purpose |
|------|---------|
| Best gestalt hinge | Highest `match` score executed faithfully |
| Simplified single-read | Same symbol, drop secondary if forced |
| Alternate hinge | Same pair, different shared edge/void |
| Wildcard | One brief-aligned surprise from symbols tail |

## CONSTRUCTION.md contents

For each SVG:

1. **File** — path
2. **Match ref** — which `MATCHES.md` row
3. **Primary / secondary read**
4. **Hinge** — coordinates or construction line (e.g. `x=256 vertical`)
5. **Build recipe** — steps: base mass → subtract void → verify winding
6. **Known weaknesses** — for critique

## SVG skeleton

```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="[Mark name]">
  <!-- primary: [read] -->
  <!-- hinge: [description] -->
  <path fill="#000000" fill-rule="evenodd" d="..." />
</svg>
```

## Verification before preview

- [ ] Renders in browser without filters
- [ ] `fill-rule="evenodd"` if using holes
- [ ] 32px mental test: squint at thumbnail — primary read holds?
- [ ] Inverse test: swap fill/background mentally

## Handoff

→ `preview` — never show raw SVG alone as final deliverable for critique.

## Do not

- Export from Figma without simplifying paths.
- Embed raster images in SVG.
- Use text as logo (that's wordmark track).
