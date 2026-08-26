# remix — combine winning traits from two candidates

Use when `critique` marks two candidates **REMIX** — each has one strength the other lacks.

## Syntax

`remix A+B` — A and B are candidate ids, filenames, or critique row names.

## Inputs

- `.logo-gestalt/CRITIQUE.md` REMIX notes (must name **specific traits**)
- SVG for A and B
- Relevant `MATCHES.md` / `CONSTRUCTION.md` rows

## Procedure

### 1. Name the traits explicitly

| From | Trait to carry |
|------|----------------|
| A | e.g. stronger 32px primary |
| B | e.g. cleaner hinge, better cube facet |

Vague "combine them" is invalid — ask user or infer from critique bullets.

### 2. Choose a structural base

Pick **one** SVG as topological base (the path you'll edit). The other donates **geometry constraints**, not a pasted layer.

### 3. Graft constraints, not shapes

- Import **hinge position** from B onto A's mass
- Import **angle family** (isometric vs orthogonal)
- Import **void size** — not whole sub-path overlay

### 4. One new candidate

Write `.logo-gestalt/marks/remix-A+B.svg` — single file, not a folder of attempts.

### 5. Preview + mini-critique

Run preview on remix only. Vision check:

- Did we get **both** named traits?
- Did we introduce collage?

### 6. Update docs

- `CONSTRUCTION.md` — remix lineage
- `CRITIQUE.md` — new row with verdict

## Rules

- Max **one** remix per pair per session unless user asks more.
- If remix fails vision → pick A or B as winner in `distill`; don't loop remixes.
- Never remix two **KILL** candidates.

## Example

> A: pointer reads well at 32px but cube feels flat.  
> B: cube depth strong but pointer muddy.  
> Remix: A's chevron proportions + B's facet angles on shared spine from `MATCHES.md` row 1.

## Handoff

→ `distill` if remix wins  
→ `construct` manual edit if remix needs deeper rebuild
