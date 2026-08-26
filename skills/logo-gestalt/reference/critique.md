# critique — vision-backed mark review

Evaluate raster previews with **vision**, not imagination. One bounded pass per `craft` run unless user requests another.

## Preconditions

- [preview.md](preview.md) completed — `board.html`, size ladder, PNGs exist.
- Load [vision.md](vision.md) and follow its protocol.

## Output

`.logo-gestalt/CRITIQUE.md` from `<skill-dir>/templates/CRITIQUE.md`.

## Vision pass (required)

1. **Role + scan** — "You are a senior identity designer reviewing monogram marks at multiple scales."
2. **Attach images** — at minimum per candidate:
   - Hero (1024)
   - favicon-sm (32)
   - Size ladder row OR side-by-side 64+32
3. For gestalt marks, attach **hinge annotation** or describe-then-judge on cropped hinge region.
4. **Multi-candidate** — one message with labeled images beats serial one-offs.
5. Record verdicts in `CRITIQUE.md`; do not rely on chat memory alone.

## Heuristics

Score each candidate 1–5 on:

| Criterion | Question |
|-----------|----------|
| **Primary read** | Instant at 32px? |
| **Gestalt hinge** | Shared geometry or collage? |
| **Originality** | Distinct from avoid-list comps? |
| **Brief fit** | Matches personality + constraints? |
| **Construction** | Simple enough to maintain? |
| **System potential** | Works mono, reversed, app icon crop? |

## Kill criteria (immediate)

Kill or send back to `construct` / `match` if:

- Reads as **two icons glued** (see [anti-patterns.md](anti-patterns.md)).
- Primary read **ambiguous** at 32px.
- Requires color or label to parse.
- Violates brief avoid list or non-negotiables.
- Hinge is decorative — removable without losing secondary read only.
- **Category cliché** unless brief wanted it.

## Verdict vocabulary

| Verdict | Meaning |
|---------|---------|
| **KEEP** | Finalist for `distill` |
| **REMIX** | Strong trait; pair with another candidate |
| **REVISE** | Fixable in `construct` (name the fix) |
| **KILL** | Do not iterate; try different match |

## Bounded pass

- Critique **all** candidates in one table.
- Max **one** revise loop per candidate in a single craft session.
- If all KILL → reroute to `symbols` or `match`, not endless tweak.

## Describe-then-judge (gestalt)

For double-read marks:

1. Ask vision model to **describe silhouette only** without naming intended symbols.
2. Compare description to intended primary/secondary.
3. Mismatch → hinge or primary hierarchy problem.

## Handoff

- Any **KEEP** → `distill`
- **REMIX** pairs → `remix`
- **REVISE** with clear hinge note → `construct` → `preview` → stop (no second critique unless asked)

## What to tell the user

Short table: candidate name, verdict, one-line reason, 32px pass/fail. Link `board.html`.
