# symbols — semantic + geometric inventory

Invent **8–16** symbol candidates from the product's own world — not clip-art categories.

## Output

`.logo-gestalt/SYMBOLS.md` from `<skill-dir>/templates/SYMBOLS.md`.

## Inputs

- `BRAND.md` symbol seeds
- `.logo-gestalt/BRIEF.md` semantic anchors and avoid list

## Method

### 1. Harvest language

From brand + brief, list domain nouns, verbs, metaphors, and **tools of the trade** (what practitioners literally manipulate). Example for a code editor: cursor, bracket, gutter, diff, merge, tree, pane, selection.

### 2. Convert to geometry primitives

For each candidate, note:

| Field | Content |
|-------|---------|
| **Name** | Short label |
| **Brand fit** | One sentence why it belongs to *this* product |
| **Primitive** | circle, triangle, chevron, cube face, bracket, stroke, dot grid, … |
| **Axes** | vertical spine, 30° isometric, radial, horizontal bar |
| **Silhouette** | filled vs outline; positive vs negative primary |
| **Scale risk** | what breaks at 32px |

### 3. Balance the set

Aim for:

- **4–6** abstract/geometric (high gestalt potential)
- **3–5** domain-literal but simplifiable
- **2–4** unexpected cross-domain (metaphor stretch)
- At least **2** strong **void** candidates (holes that could host a second read)

### 4. Pre-filter

Drop any symbol that:

- Is category cliché (lightbulb "ideas", rocket "startup") unless brief explicitly wants subversion.
- Has no plausible 2D silhouette.
- Only works with color or label.

Mark dropped items in a `## Rejected` section with one-line reason.

## Geometry notes (required depth)

Each symbol needs enough notes that `match` can decompose without re-inventing:

```
### Cursor (pointer)
- Primitive: chevron + stem; acute tip ~25–35°
- Shared-edge candidate: vertical stem (full height)
- Void: interior of chevron if stem removed
- Isometric friend: stem aligns to cube front edge at 0° screen vertical
```

## Quality bar

Another designer could sketch rough thumbnails from `SYMBOLS.md` alone. Every entry has **primitive + axis**, not just a metaphor name.

## Handoff

→ `match` to pair and score shared geometry.

Do not pair symbols in this file — that's `match`.
