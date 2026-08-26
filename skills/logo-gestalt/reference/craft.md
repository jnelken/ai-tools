# craft — full logo gestalt pipeline

Run this when the user wants a logo designed end-to-end, or says "design a logo for…" without naming a sub-command.

## Preconditions

1. Load [anti-patterns.md](anti-patterns.md) and skim [gestalt.md](gestalt.md).
2. Confirm project layout:
   - `BRAND.md` at repo root (create via `init` if missing and the brief is real).
   - `.logo-gestalt/` for working artifacts (create on first write).
3. Copy templates from `<skill-dir>/templates/` when a file does not exist yet.

## Pipeline overview

```
init/brief → symbols → match → construct → preview → critique → distill/polish
```

Each stage has a dedicated reference. **Do not skip** `preview` before `critique` — critique is vision work ([vision.md](vision.md)).

| Stage | Reference | Primary output |
|-------|-----------|----------------|
| Brand context | [init.md](init.md) | `BRAND.md` |
| Logo brief | [brief.md](brief.md) | `.logo-gestalt/BRIEF.md` |
| Symbol inventory | [symbols.md](symbols.md) | `.logo-gestalt/SYMBOLS.md` |
| Visual matching | [match.md](match.md) | `.logo-gestalt/MATCHES.md` |
| SVG candidates | [construct.md](construct.md) | `.logo-gestalt/marks/*.svg`, `CONSTRUCTION.md` |
| Raster board | [preview.md](preview.md) | `previews/`, `board.html`, `size-ladder.html` |
| Vision critique | [critique.md](critique.md) + [vision.md](vision.md) | `.logo-gestalt/CRITIQUE.md` |
| Narrow field | [distill.md](distill.md) | 1–3 finalists |
| Delivery | [polish.md](polish.md) | mark system notes, lockups |

## Step-by-step

### 1. init / brief (thinking before drawing)

- If `BRAND.md` is thin or missing → run [init.md](init.md) (short interview, fill `templates/BRAND.md`).
- Always produce or refresh `.logo-gestalt/BRIEF.md` via [brief.md](brief.md).
- **Gate:** user (or pinned brief) confirms constraints before symbols. No SVG yet.

### 2. symbols

Follow [symbols.md](symbols.md). Target **8–16** entries: semantic name, why it fits the brand, primitive geometry, axis/angle notes.

### 3. match

Follow [match.md](match.md). Decompose top symbols into edges, angles, voids. Score pairs 0–5 on **shared vectors**. Shortlist **3–5** pairings with construction hints.

### 4. construct

Follow [construct.md](construct.md). **Mono-first** SVG in a square viewBox (default `0 0 512 512`). Produce **3–5** candidates under `.logo-gestalt/marks/`. Document hinge lines in `CONSTRUCTION.md`.

### 5. preview (required before critique)

Follow [preview.md](preview.md). Prefer the skill script:

```bash
python3 <skill-dir>/scripts/build_previews.py \
  --marks-dir .logo-gestalt/marks \
  --out-dir .logo-gestalt/previews \
  --board .logo-gestalt/board.html
```

Load [vision.md](vision.md) — you will use these images in the next step.

### 6. critique

Follow [critique.md](critique.md). **Vision pass required:** attach `board.html` renders, size ladder, and hinge crops. Fill `templates/CRITIQUE.md` → `.logo-gestalt/CRITIQUE.md`.

Bounded pass: one critique round per craft run unless the user asks for another. Kill weak candidates explicitly.

### 7. distill / polish

- **distill** ([distill.md](distill.md)): reduce to 1–3 marks; delete decorative strokes; verify primary read at 32px.
- **polish** ([polish.md](polish.md)): spacing, clear space, mono/color variants, favicon-safe simplification, delivery checklist.
- Optional **remix** ([remix.md](remix.md)) if two candidates each have one winning trait.

## What to show the user

Prefer **board + shortlist table** over a long essay:

1. Link or embed `board.html` (hero + icon + favicon columns).
2. Top 3–5 matches with scores from `MATCHES.md`.
3. Critique verdict per candidate (keep / remix / kill).
4. One recommended next step (distill winner, remix A+B, or revisit symbols).

## Failure modes → reroute

| Symptom | Reroute |
|---------|---------|
| Matches all score ≤2 | `symbols` — invent more geometric primitives |
| Primary read unclear at 32px | `construct` — simplify; then `preview` |
| Secondary read forced | `match` — different pair or accept single-read mark |
| Collage feeling | [anti-patterns.md](anti-patterns.md); back to `match` |
| Critique without images | Stop — run `preview` first |

## Calibration

When explaining gestalt craft to the user, point at [examples/cursor-case.md](../examples/cursor-case.md) and `docs/sample-marks/hinge-diagram.svg`.
