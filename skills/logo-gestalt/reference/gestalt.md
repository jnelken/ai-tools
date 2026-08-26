# gestalt — figure-ground and shared geometry

Gestalt logo craft is **one silhouette, multiple readings** — achieved by shared edges, angles, and voids, not transparency stacks.

## Core vocabulary

### Figure–ground

- **Figure** — the shape that reads first (positive mass).
- **Ground** — the field; can flip when a void reads as figure.
- **Stable flip** — secondary read appears on **discovery**, not on squinting.

### Shared edge

One path segment serves two symbols. The Cursor mark's vertical spine is simultaneously the cursor stem and the cube's front vertical edge. Removing it breaks **both** reads.

### Shared angle

Two primitives meet at the same angle so the eye continues the line: isometric 30° facet continuing into a chevron wing.

### Shared void

The hole in shape A is already the silhouette of B (or B's negative). Common in lettermarks (bowl of `b` as eye).

### Hinge

The **minimal** geometric element that carries the double read — usually one edge, one axis, or one void. Annotate hinges in `CONSTRUCTION.md` and preview boards.

## Evaluation questions

1. If I delete the hinge, do both readings collapse?
2. At 32px black-on-white, is the **primary** read obvious in &lt;1s?
3. Does the secondary read use **existing** contours or a pasted mini-icon?
4. Would a child describe one shape or two glued shapes?

## Cursor calibration (summary)

Full walkthrough: [examples/cursor-case.md](../examples/cursor-case.md). Diagram: `docs/sample-marks/hinge-diagram.svg`.

| Element | Role |
|---------|------|
| Isometric cube (hex silhouette) | Primary mass; dev-tool solidity |
| Cursor chevron | Carved from cube face; negative space + wing |
| Vertical at x=center | **Hinge** — shared spine |
| 30° facets | Wing aligns with isometric family |

The mark fails gestalt if the pointer is a separate triangle **on top of** the cube instead of **cut from** the same solid.

## Scoring alignment

`match` scores 5 when hinge is a **single construction constraint**, not a composition trick.

## When to abandon double-read

- Brief asks for instant legibility over cleverness.
- No pair scores ≥3 after two symbol passes.
- Favicon test kills secondary read anyway — ship a strong single read.

## Related

- [match.md](match.md) — formal scoring
- [anti-patterns.md](anti-patterns.md) — collage traps
- [construct.md](construct.md) — mono SVG execution
