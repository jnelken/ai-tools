# brief — logo brief without drawing

Produce a **confirmed logo brief** before any symbol inventory or SVG. No sketches, no "here are three concepts."

## Output

`.logo-gestalt/BRIEF.md` from `<skill-dir>/templates/BRIEF.md`.

Create `.logo-gestalt/` if needed.

## Inputs

- `BRAND.md` (required for serious runs)
- User's explicit request this session
- Any pinned constraints ("must include letter M", "no red")

## Interview (logo-specific)

Ask only what `BRAND.md` does not already answer:

1. **Mark type** — logomark only, wordmark, monogram, combination lockup?
2. **Primary surfaces** — app icon, favicon, social avatar, print, signage?
3. **Reading goal** — single read vs deliberate double-read (gestalt)?
4. **Geometry bias** — angular vs round; literal vs abstract; symmetric vs asymmetric?
5. **Semantic anchors** — 2–4 product nouns/verbs to explore (not final shapes).
6. **Avoid list** — clichés, competitor shapes, category traps.
7. **Success test** — how will we know the mark works? (e.g. "recognizable in Slack sidebar at 24px").
8. **Deliverables** — SVG only, full mark system, animation-ready?

## Procedure

1. Copy template → `.logo-gestalt/BRIEF.md`.
2. Sync **Constraints** and **Avoid** with `BRAND.md` non-negotiables — brief must not contradict brand.
3. Write **Exploration scope**: how many directions (`symbols` count), gestalt ambition (single vs double read).
4. Add **Open questions** — max 3; resolve with user before `symbols`.
5. End with **Approval** checkbox line: `Brief confirmed: [ ] yes  [ ] needs revision`.

## Gate

Do not run `symbols` until:

- Open questions are empty or explicitly deferred by user, and
- Brief confirmed (user said yes, or constraints were given upfront and acknowledged).

## Anti-scope

The brief is **not**:

- A mood board of finished logos to copy.
- A color palette spec (note preferences only).
- Construction geometry (that's `match` / `construct`).

## Handoff

- → `symbols` for inventory.
- → `craft` continues automatically after brief if user asked for full pipeline.
