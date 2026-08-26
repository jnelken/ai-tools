# distill — strip to the inevitable few marks

After critique **KEEP** verdicts, reduce noise and pick **1–3** finalists.

## Inputs

- `.logo-gestalt/CRITIQUE.md` KEEP rows
- SVG sources in `.logo-gestalt/marks/`
- Size ladder from preview

## Procedure

### 1. Eliminate redundancy

If two KEEP candidates differ only by minor corner radius or 2° angle, merge — keep the simpler path count.

### 2. Subtraction pass

For each finalist, list every element; remove one at a time mentally:

- If primary read survives → delete element in SVG.
- If only secondary dies → good (prefer strong primary).
- Stop when next removal breaks 32px.

### 3. Optical correction

- Center mass by eye, not bounding-box center.
- Equalize negative space at icon crop (square safe zone).
- Snap hinge to consistent x/y when multiple finalists share a system.

### 4. Rename and promote

```
.logo-gestalt/marks/final-1.svg
.logo-gestalt/marks/final-2.svg   (optional)
```

Archive killed SVGs to `.logo-gestalt/marks/archive/` or delete per user preference.

### 5. Re-preview

Run `build_previews.py` on finals only. Update `board.html`.

### 6. Decision record

Short section in `CRITIQUE.md` or `BRIEF.md`:

- **Winner** (if chosen) + one sentence why
- **Runner-up** trait worth preserving for remix

## Quality bar

Finalists should have **fewer nodes** than first construct pass. If node count grew, distillation failed.

## Handoff

→ `polish` for mark system and delivery
→ `remix` if two finalists have complementary traits and no clear winner

## When to stop at one

Brief asked single mark, or one finalist wins every criterion including 32px and gestalt.
