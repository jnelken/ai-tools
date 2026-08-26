# Cursor mark — gestalt calibration case study

Use this file to calibrate `match` scoring, `construct` hinges, and `critique` vision passes. The Cursor logomark is not official construction spec — it is a **teaching example** of shared-vector craft.

## Brand context (abbreviated)

- **Product:** AI-native code editor (Cursor)
- **Personality:** precise, craft, developer-trusted, not playful consumer
- **Mark job:** App icon, favicon, sidebar avatar — must read at 16–32px
- **Semantic pair:** **cursor** (pointer) + **cube** (structure, 3D/build space)

## Symbol decomposition

### Cursor (pointer)

| Piece | Geometry |
|-------|----------|
| Stem | Vertical segment, full height of chevron |
| Wings | Two facets meeting at tip; ~25–35° interior angle |
| Tip | Acute point, upper-right quadrant in default orientation |

### Isometric cube (hex silhouette)

| Piece | Geometry |
|-------|----------|
| Front face | Parallelogram; vertical **front edge** at center |
| Top face | 30° isometric facet |
| Bottom face | 30° isometric facet |
| Outer hex | Six-edge silhouette when flattened to 2D |

## Match score: 5 / 5

| Shared vector | Detail |
|---------------|--------|
| **Shared edge** | Cursor stem = cube front vertical edge (one line, two meanings) |
| **Shared angle** | Cursor wing aligns with isometric facet direction |
| **Void** | Chevron interior carved from cube face mass |
| **Silhouette** | Single outer hex — not cube + triangle overlay |

**Hinge:** vertical through optical center (in teaching diagram, x=256 in 512 viewBox).

See `docs/sample-marks/hinge-diagram.svg` for annotated hinge.

## Primary vs secondary read

| Order | Read | Condition |
|-------|------|-----------|
| **Primary** | Isometric cube / hex solid | Black mass, app icon context |
| **Secondary** | Cursor pointer | Discovered on focus; wing cuts read as pointer |

At 32px, **cube** must win; pointer can simplify to a notch — still acceptable if cube reads.

## Construction recipe (mono)

1. Draw isometric hex mass (single filled path).
2. Subtract cursor chevron void from front face (boolean or even-odd path).
3. **Do not** add a separate triangle path on top.
4. Verify: deleting center vertical breaks cube corner **and** stem.

## Collage failure mode

Wrong approach:

- Gray cube + white triangle pasted upper-right
- Two paths with overlap instead of subtraction
- Stem that does not continue through cube interior

Vision test: describe-then-judge should say "hexagon with cutout" not "hexagon and triangle."

## Critique checklist (Cursor-class)

- [ ] 32px: hex/cube mass obvious
- [ ] Hinge is one vertical (shared)
- [ ] No orphan paths
- [ ] Works `#000` on white and `#fff` on black
- [ ] Secondary optional at favicon — primary still on-brief

## Using in other briefs

When scoring your own pairs, ask: **Is our hinge as load-bearing as Cursor's spine?** If removing one edge kills only one read, score ≤3.

## Related files

- [reference/gestalt.md](../reference/gestalt.md)
- [reference/match.md](../reference/match.md)
- [docs/sample-marks/hinge-diagram.svg](../docs/sample-marks/hinge-diagram.svg)
