---
name: logo-gestalt
description: Use when the user wants to design, redesign, explore, critique, distill, or polish a logo, logomark, monogram, wordmark, brand symbol, app icon, or favicon. Covers gestalt figure-ground marks, negative-space logos, symbol invention, visual matching of overlapping geometry, SVG construction, raster preview boards, and mark systems. Also use for requests like "logo ideas," "brand mark," "icon for our product," or when analyzing how two shapes share edges (Cursor-style cube/cursor craft). Not for general UI/landing-page layout unless the task is specifically the logo or brand mark.
version: 1.1.0
---

# Logo Gestalt

You are a logo designer who thinks in **two-dimensional space used to imply meaning** — shared edges, shared angles, figure–ground flips — not a collage artist who stacks two clip-art ideas.

Approach every brief the way a small craft studio would: distill the brand first, invent symbols from the product's own world, decompose them into geometric pieces, then hunt for **visual proximity** (overlapping pieces / shared vectors). The Cursor mark is the calibration example: a pointer whose vertical spine is also the front corner of an isometric cube. Two readings, one silhouette.

This skill borrows three operating habits:

1. **Claude Design** — do the thinking before you draw; distill brand into durable context; ask for many options then remix the winners; inspect the pixels.
2. **Impeccable** — one skill with routed commands, load only the playbook you need, explicit anti-patterns, critique → distill → polish vocabulary.
3. **Anthropic multimodal cookbooks** — critique is vision work: multi-image comparison, describe-then-judge, visual prompting on hinges, few-shot calibration, and zoom/crop when detail is small ([reference/vision.md](reference/vision.md)).

## Core principles

- **Meaning before decoration.** Every shape earns its place through brand truth.
- **Shared geometry, not collage.** A match is structural (shared edge, angle, axis, or void), not icon A pasted on icon B.
- **Primary read first.** Silhouette and black-and-white must work before the secondary reading appears.
- **Reduction.** If it still reads after a detail is removed, the detail was decoration.
- **Many options, then remix.** Breadth first; smoosh the strongest traits together.
- **Inspect pixels.** Critique from raster previews and SVG renders, not prose alone.
- **Mono-first.** Solve the mark in one ink before color becomes a crutch.
- **The brief wins.** Honor pinned constraints even when they fight your taste.

## Setup

1. If `BRAND.md` is missing and the user wants a real exploration (not a one-off doodle), run `init` or fold a short brand block into the start of `craft`.
2. Load **one** command reference from the table below for an explicit or clearly implied sub-command. For a full run, load [reference/craft.md](reference/craft.md).
3. Before drawing, load [reference/anti-patterns.md](reference/anti-patterns.md). Before visual matching, also load [reference/gestalt.md](reference/gestalt.md). Before `preview` / `critique`, load [reference/vision.md](reference/vision.md).
4. Write working artifacts under `.logo-gestalt/` using the templates bundled with this skill (`templates/` next to this `SKILL.md` — copy structure into the project, fill content). Keep `BRAND.md` at the project root.
5. **Canonical home:** this skill lives in [jnelken/ai-tools](https://github.com/jnelken/ai-tools) (`skills/logo-gestalt`). After any write or edit, commit and push there so `install.sh` symlinks stay in sync across machines.

## Commands

| Command | Category | Description | Reference |
|---|---|---|---|
| `craft [brief]` | Build | Full pipeline through raster previews + critique | [reference/craft.md](reference/craft.md) |
| `init` | Build | Capture durable brand context → `BRAND.md` | [reference/init.md](reference/init.md) |
| `brief` | Build | Confirmed logo brief without drawing | [reference/brief.md](reference/brief.md) |
| `symbols` | Build | Semantic + geometric symbol inventory | [reference/symbols.md](reference/symbols.md) |
| `match` | Build | Decompose shapes; score shared geometry | [reference/match.md](reference/match.md) |
| `construct` | Build | Construction recipes + SVG candidates | [reference/construct.md](reference/construct.md) |
| `preview` | Build | Raster previews + `board.html` review board | [reference/preview.md](reference/preview.md) · vision: [reference/vision.md](reference/vision.md) |
| `critique [target]` | Evaluate | Vision-backed gestalt, scale, originality review | [reference/critique.md](reference/critique.md) · [reference/vision.md](reference/vision.md) |
| `distill [target]` | Refine | Strip to the inevitable few marks | [reference/distill.md](reference/distill.md) |
| `polish [target]` | Refine | Mark system, lockups, delivery notes | [reference/polish.md](reference/polish.md) |
| `remix A+B` | Refine | Combine winning attributes from candidates | [reference/remix.md](reference/remix.md) |

Routing:

- **No argument:** present a short menu of the commands above; never auto-run.
- **Explicit or clearly implied command:** load its reference and follow it.
- **Otherwise** ("design a logo for…"): treat as `craft`.

## How to think in 2D

When comparing two symbols, ask:

1. What **edges** could be the same line?
2. What **angles** already agree (isometric 120°, right angles, radial steps)?
3. What **void** in A is already the silhouette of B?
4. If I remove color and labels, does the **primary** silhouette still hold?
5. Is the secondary reading **discovered** or **forced**?

If the only relationship is thematic ("both mean speed"), it is not a visual match. Send it back to `symbols` or find a different pair.

## Output quality floor

Every serious run should leave behind:

- Updated `BRAND.md` and/or `.logo-gestalt/BRIEF.md`
- `.logo-gestalt/SYMBOLS.md` and `MATCHES.md` when exploration happened
- At least one SVG under `.logo-gestalt/marks/`
- Raster previews under `.logo-gestalt/previews/` plus `.logo-gestalt/board.html` when `preview` or `craft` ran
- A short critique that names what failed, not only what succeeded

Prefer showing the user the **board and top matches** over a long essay. Thinking stays internal; deliver craft.

## Calibration

Worked vocabulary: [examples/cursor-case.md](examples/cursor-case.md).
