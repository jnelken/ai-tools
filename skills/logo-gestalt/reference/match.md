# match — decompose, score, shortlist

Find **visual proximity** between symbols — shared edges, angles, axes, voids — not thematic similarity.

## Output

`.logo-gestalt/MATCHES.md` from `<skill-dir>/templates/MATCHES.md`.

## Procedure

### 1. Pick symbols to decompose

From `SYMBOLS.md`, take the **top 8–10** by gestalt potential (abstract + strong axes/voids). Skip rejected entries.

### 2. Decompose each symbol

For each symbol, list atomic pieces:

- **Edges** — line segments with direction (e.g. `V spine: vertical, x=center`)
- **Angles** — corners, isometric 120°, 45°, 90°
- **Voids** — negative regions that could read as another shape
- **Mass blocks** — filled regions with bounding behavior

Use consistent vocabulary from [gestalt.md](gestalt.md).

### 3. Cross-pair and score

For each unordered pair (A, B), score **shared vectors** 0–5:

| Score | Meaning |
|-------|---------|
| 0 | Thematic only; no structural overlap |
| 1 | Weak rhyme (similar category, different geometry) |
| 2 | One edge or angle could align with effort |
| 3 | Clear shared edge OR void-host relationship |
| 4 | Multiple shared constraints; plausible mono silhouette |
| 5 | Inevitable hinge (Cursor-class: one stroke, two reads) |

Record in a table:

```
| Pair | Shared vectors | Score | Hinge note |
|------|----------------|-------|------------|
| cursor × cube | vertical spine = front corner; 30° facet agrees with chevron wing | 5 | see cursor-case |
```

### 4. Shortlist 3–5

Take highest scores ≥3. For each shortlist entry write:

- **Primary read** — what you see first (1 phrase)
- **Secondary read** — discovered read, or "none"
- **Hinge** — the exact shared geometry (one sentence)
- **Construction hint** — mono SVG strategy (union, subtract, single path)
- **Risk** — what could feel like collage

### 5. Kill table

Pairs scored 0–2 get one line in `## Low scores` — helps avoid revisiting.

## Rules

- **No drawing yet.** This file is geometry reasoning only.
- If **no pair ≥3**, stop and send back to `symbols` with a note on what's missing (more axes? more voids?).
- Prefer pairs where **one** element is the hinge, not two equal icons overlapping.

## Calibration

Study [examples/cursor-case.md](../examples/cursor-case.md) — cursor × isometric cube scores 5 because the vertical spine is **literally the same segment** as the cube's front corner.

## Handoff

→ `construct` for 3–5 SVG candidates from the shortlist (not every pair — pick best 3–5 directions).
